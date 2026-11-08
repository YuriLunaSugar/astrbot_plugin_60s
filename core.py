"""Framework-independent daily delivery engine; all external I/O is injectable."""
import asyncio
import copy
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo


class ApiError(Exception):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def upgrade_config(config):
    """Keep legacy data intact while making it editable without a template menu."""
    updated = copy.deepcopy(dict(config))
    value = updated.get("target_formats")
    if isinstance(value, (dict, list)):
        updated["target_formats"] = json.dumps(value, ensure_ascii=False, indent=2)
    return updated


@dataclass
class Settings:
    schedules: dict = field(default_factory=dict)
    target_formats: dict | list | str = field(default_factory=list)
    formats: dict = field(default_factory=dict)
    image_hosts: list = field(default_factory=lambda: ["mmbiz.qpic.cn"])
    enabled: bool = False
    base_url: str = "https://60s.viki.moe"
    timezone: str = "Asia/Shanghai"
    send_time: str = "09:00"
    targets: tuple = ()
    daily_enabled: bool = True
    history_enabled: bool = True
    catchup_minutes: int = 120
    timeout_seconds: int = 20

    def __post_init__(self):
        if not isinstance(self.formats, dict) or any(not isinstance(v, str) for v in self.formats.values()):
            raise ValueError("formats 必须为接口到发送格式的映射")
        if not isinstance(self.image_hosts, list) or any(not isinstance(h, str) or not h for h in self.image_hosts):
            raise ValueError("image_hosts 必须为域名列表")
        for field in ("enabled", "daily_enabled", "history_enabled"):
            if type(getattr(self, field)) is not bool:
                raise ValueError(f"{field} 必须为布尔值")
        self.zone = ZoneInfo(self.timezone)
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", self.send_time):
            raise ValueError("send_time 必须为 HH:MM")
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname or url.query or url.fragment or url.username:
            raise ValueError("base_url 必须是无账号、查询参数的 HTTP(S) 服务基址")
        if not 1 <= self.catchup_minutes <= 1440 or not 1 <= self.timeout_seconds <= 120:
            raise ValueError("补发窗口须为 1–1440 分钟，超时须为 1–120 秒")
        if not isinstance(self.targets, (list, tuple)) or any(not isinstance(t, str) or len(t.split(":", 2)) != 3 or not all(t.split(":", 2)) for t in self.targets):
            raise ValueError("targets 必须为完整 unified_msg_origin 字符串列表")
        self.targets = tuple(dict.fromkeys(self.targets))
        if isinstance(self.target_formats, str):
            try:
                self.target_formats = json.loads(self.target_formats)
            except json.JSONDecodeError as exc:
                raise ValueError(f"target_formats 必须是有效 JSON：第 {exc.lineno} 行第 {exc.colno} 列；无覆盖项请填 []") from exc
        if isinstance(self.target_formats, list):
            mappings = {}
            for entry in self.target_formats:
                if not isinstance(entry, dict) or not isinstance(entry.get("target"), str):
                    raise ValueError("目标格式条目必须包含 target")
                target = entry["target"]
                if target in mappings:
                    raise ValueError("同一目标不能重复配置格式")
                if set(entry) - {"target", "daily", "history", "__template_key"}:
                    raise ValueError("目标格式条目只允许两个日更接口")
                mappings[target] = {k: entry[k] for k in ("daily", "history") if entry.get(k)}
            self.target_formats = mappings
        if not isinstance(self.target_formats, dict):
            raise ValueError("target_formats 必须为目标会话到日更格式的映射")
        for target, mapping in self.target_formats.items():
            if target not in self.targets or not isinstance(mapping, dict):
                raise ValueError("target_formats 中的目标必须存在于 targets，值必须是对象")
            if set(mapping) - {"daily", "history"}:
                raise ValueError("目标格式仅支持 daily/history")
            for kind, fmt in mapping.items():
                allowed = ("text", "json", "markdown", "image") if kind == "daily" else ("text", "json", "markdown")
                if fmt not in allowed:
                    raise ValueError(f"{kind} 不支持发送格式 {fmt}")
        if not isinstance(self.schedules, dict) or set(self.schedules) - {"daily", "history"}:
            raise ValueError("schedules 仅支持 daily/history")
        for kind, schedule in self.schedules.items():
            if not isinstance(schedule, dict):
                raise ValueError("每个接口的调度必须为对象")
            for key in ("send_time", "end_time"):
                value = schedule.get(key, "")
                if value and (not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value)):
                    raise ValueError(f"{kind}.{key} 必须为 HH:MM")
            wait = schedule.get("retry_minutes", 15)
            if type(wait) is not int or not 1 <= wait <= 1440:
                raise ValueError("retry_minutes 必须为 1–1440 的整数分钟")
            if schedule.get("end_time") == (schedule.get("send_time") or self.send_time):
                raise ValueError("接口结束时间不能与开始时间相同")

    def format_for(self, kind, target):
        return self.target_formats.get(target, {}).get(kind, self.formats.get(kind, "text"))

    def retry_seconds(self, kind):
        return self.schedules.get(kind, {}).get("retry_minutes", 15) * 60

    def window(self, kind, now):
        """Return schedule-day and inclusive end; old configs retain old boundaries."""
        local = now.astimezone(self.zone)
        schedule = self.schedules.get(kind, {})
        start = schedule.get("send_time") or self.send_time
        end = schedule.get("end_time")
        hour, minute = map(int, start.split(":"))
        if not end:
            # Compatibility: original catchup window stops at midnight and excludes end.
            begin = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), self.zone)
            deadline = min(begin.timestamp() + self.catchup_minutes * 60, midnight.timestamp())
            if begin.timestamp() <= now.timestamp() < deadline:
                return local.date(), deadline - 0.000001
            return None
        end_hour, end_minute = map(int, end.split(":"))
        wraps = (end_hour, end_minute) < (hour, minute)
        for day in (local.date(), local.date() - timedelta(days=1)):
            begin = datetime.combine(day, datetime.min.time(), self.zone).replace(hour=hour, minute=minute)
            finish_day = day + timedelta(days=1) if wraps else day
            finish = datetime.combine(finish_day, datetime.min.time(), self.zone).replace(hour=end_hour, minute=end_minute)
            if begin.timestamp() <= now.timestamp() <= finish.timestamp():
                return day, finish.timestamp()
        return None

    def due_day(self, now):
        local = now.astimezone(self.zone)
        hour, minute = map(int, self.send_time.split(":"))
        scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elapsed = local.timestamp() - scheduled.timestamp()
        return local.date() if 0 <= elapsed < self.catchup_minutes * 60 else None


class Store:
    """Claim before sending: ambiguous delivery is never automatically retried."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS deliveries (day TEXT, target TEXT, kind TEXT, source_date TEXT, status TEXT, PRIMARY KEY(day,target,kind))")
        self.db.execute("CREATE TABLE IF NOT EXISTS schedule_state (kind TEXT PRIMARY KEY, next_fetch REAL NOT NULL, last_error TEXT)")
        self.db.commit()

    def has(self, day, target, kind):
        return self.db.execute("SELECT 1 FROM deliveries WHERE day=? AND target=? AND kind=?", (str(day), target, kind)).fetchone() is not None

    def eligible(self, day, target, kind):
        row = self.db.execute(
            "SELECT status FROM deliveries WHERE day=? AND target=? AND kind=?",
            (str(day), target, kind),
        ).fetchone()
        return row is None or row[0] == "retryable"

    def claim(self, day, target, kind, source):
        with self.db:
            return self.db.execute(
                "INSERT INTO deliveries VALUES (?,?,?,?,?) "
                "ON CONFLICT(day,target,kind) DO UPDATE SET "
                "source_date=excluded.source_date,status='pending' "
                "WHERE deliveries.status='retryable'",
                (str(day), target, kind, source, "pending"),
            ).rowcount == 1

    def load_schedule_state(self):
        return self.db.execute("SELECT kind,next_fetch,last_error FROM schedule_state").fetchall()

    def save_schedule_state(self, kind, deadline, error):
        with self.db:
            self.db.execute(
                "INSERT INTO schedule_state VALUES (?,?,?) "
                "ON CONFLICT(kind) DO UPDATE SET next_fetch=excluded.next_fetch,last_error=excluded.last_error",
                (kind, deadline, error),
            )

    def finish(self, day, target, kind, status):
        with self.db:
            self.db.execute("UPDATE deliveries SET status=? WHERE day=? AND target=? AND kind=?", (status, str(day), target, kind))

    def close(self):
        self.db.close()


def render(kind, payload, expected):
    if not isinstance(payload, dict) or payload.get("code") != 200 or not isinstance(payload.get("data"), dict):
        raise ApiError("API 业务错误或数据格式无效")
    data = payload["data"]
    source = data.get("date")
    if not isinstance(source, str):
        raise ApiError("API 缺少真实日期")
    if kind == "daily":
        try:
            actual = date.fromisoformat(source)
        except ValueError as exc:
            raise ApiError("日报日期无效") from exc
        if actual != expected:
            raise ApiError(f"日报返回 {source}，计划日期 {expected}；跳过旧版/异日内容")
        items = data.get("news")
        if not isinstance(items, list):
            raise ApiError("日报 news 为空或无效")
        lines = [x.strip() for x in items if isinstance(x, str) and x.strip()]
        title = "每天60秒读懂世界"
    else:
        try:
            month, day = map(int, source.split("-"))
        except (ValueError, TypeError) as exc:
            raise ApiError("历史日期无效") from exc
        if (month, day) != (expected.month, expected.day):
            raise ApiError(f"历史返回日期 {source} 与计划日期不符")
        items = data.get("items")
        if not isinstance(items, list):
            raise ApiError("历史 items 为空或无效")
        lines = [f"{x.get('year') or '年份不详'}年：{x['title'].strip()}" for x in items if isinstance(x, dict) and isinstance(x.get("title"), str) and x["title"].strip()]
        title = "历史上的今天"
    if not lines:
        raise ApiError("无有效内容，跳过发送")
    # One bounded message per content/target prevents partial multi-message retries.
    text = f"{title}｜接口日期：{source}\n" + "\n".join(f"{i}. {line}" for i, line in enumerate(lines, 1))
    if len(text) > 3500:
        text = text[:3450] + "\n（内容过长，已截断）"
    return source, text


def retry_delay(value, now=None):
    try:
        return max(900, int(value))
    except (TypeError, ValueError):
        try:
            return max(900, int((parsedate_to_datetime(value) - (now or datetime.now(UTC))).total_seconds()))
        except (TypeError, ValueError, OverflowError):
            return 900


class HttpClient:
    def __init__(self, settings, session):
        self.settings, self.session = settings, session

    async def fetch(self, kind):
        endpoint = "60s" if kind == "daily" else "today-in-history"
        return await self.fetch_path(endpoint)

    async def fetch_path(self, endpoint):
        path, _, query = endpoint.partition("?")
        params = dict(parse_qsl(query))
        params["encoding"] = "json"
        async with self.session.get(f"{self.settings.base_url.rstrip('/')}/v2/{path}", params=params, allow_redirects=False) as response:
            if response.status == 429:
                raise ApiError("HTTP 429 限流", retry_delay(response.headers.get("Retry-After")))
            if response.status != 200:
                raise ApiError(f"HTTP {response.status}")
            raw = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                raw.extend(chunk)
                if len(raw) > 1_000_000:
                    raise ApiError("响应超过 1MB")
            try:
                return json.loads(raw)
            except (ValueError, UnicodeError) as exc:
                raise ApiError("响应不是有效 JSON") from exc


class Engine:
    def __init__(self, settings, store, client, send, log, prepare=None, clock=None):
        self.settings, self.store, self.client = settings, store, client
        self.send, self.log, self.prepare, self.clock = send, log, prepare, clock
        state = store.load_schedule_state()
        self.next_fetch = {kind: deadline for kind, deadline, _ in state}
        self.last_error = {kind: error for kind, _, error in state if error}
        self.lock = asyncio.Lock()

    def _cooldown(self, kind, current, error=None, retry_after=0):
        deadline = current.timestamp() + max(self.settings.retry_seconds(kind), retry_after)
        self.store.save_schedule_state(kind, deadline, error)
        self.next_fetch[kind] = deadline
        if error:
            self.last_error[kind] = error
        else:
            self.last_error.pop(kind, None)

    async def tick(self, now):
        async with self.lock:
            s = self.settings
            if not s.enabled or not s.targets:
                return
            tasks = []
            for kind, enabled in (("daily", s.daily_enabled), ("history", s.history_enabled)):
                window = s.window(kind, now)
                if enabled and window:
                    tasks.append(asyncio.create_task(self._deliver_kind(kind, window, now)))
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise

    async def _deliver_kind(self, kind, window, now):
        s = self.settings
        day, end = window
        current = self.clock or (lambda: now)
        pending = [t for t in s.targets if self.store.eligible(day, t, kind)]
        if not pending or current().timestamp() > end or current().timestamp() < self.next_fetch.get(kind, 0):
            return
        # Persist a retry reservation before I/O so restart doesn't cause a tight loop.
        self._cooldown(kind, current())
        try:
            payload = await asyncio.wait_for(self.client.fetch(kind), s.timeout_seconds)
            source, text = render(kind, payload, day)
        except Exception as exc:
            self._cooldown(kind, current(), str(exc), getattr(exc, "retry_after", 0))
            self.log(f"{kind} 获取失败: {exc}")
            return
        prepared = {}
        error = None
        format_retry_after = 0
        for target in pending:
            if current().timestamp() > end:
                break
            fmt = s.format_for(kind, target)
            if fmt not in prepared:
                try:
                    prepared[fmt] = (
                        await asyncio.wait_for(self.prepare(kind, payload, source, text, target), s.timeout_seconds)
                        if self.prepare is not None else text
                    )
                except Exception as exc:
                    prepared[fmt] = exc
                    format_retry_after = max(format_retry_after, getattr(exc, "retry_after", 0))
            output = prepared[fmt]
            if isinstance(output, Exception):
                error = f"格式准备失败: {output}"
                self.log(f"{kind} {error}")
                continue
            if current().timestamp() > end:
                break
            if not self.store.claim(day, target, kind, source):
                continue
            try:
                result = await asyncio.wait_for(self.send(target, output), s.timeout_seconds)
            except Exception as exc:
                # Exceptions may happen after remote delivery. Never assume rejection.
                self.store.finish(day, target, kind, "failed_or_unknown")
                self.log(f"{kind} 发送结果不明，需人工核查: {type(exc).__name__}")
            else:
                if result is False:
                    self.store.finish(day, target, kind, "retryable")
                    error = "平台明确返回 False，等待后重试未成功目标"
                    self.log(f"{kind} {error}")
                else:
                    self.store.finish(day, target, kind, "sent")
        if any(self.store.eligible(day, target, kind) for target in s.targets):
            self._cooldown(kind, current(), error, format_retry_after)
        else:
            self.store.save_schedule_state(kind, 0, None)
            self.next_fetch.pop(kind, None)
            self.last_error.pop(kind, None)
