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
    """Migrate the legacy allowlist once; never resurrect removed destinations."""
    updated = copy.deepcopy(dict(config))
    if updated.get("destinations_migrated") is True:
        return updated
    legacy = Settings(**{k: updated[k] for k in Settings.__dataclass_fields__
                         if k in updated and k not in ("destinations", "destinations_migrated")})
    selected = Settings(destinations=updated.get("destinations", []))
    entries = {target: {"__template_key": "target", "target": target,
                       "daily": legacy.format_for("daily", target) if legacy.daily_enabled else "off",
                       "history": legacy.format_for("history", target) if legacy.history_enabled else "off"}
               for target in legacy.targets}
    for entry in entries.values():
        for kind in ("daily", "history"):
            schedule = legacy.schedules.get(kind, {})
            entry[f"{kind}_send_time"] = schedule.get("send_time") or legacy.send_time
            entry[f"{kind}_retry_minutes"] = schedule.get("retry_minutes", 15)
            entry[f"{kind}_end_time"] = schedule.get("end_time") or ""
            entry[f"{kind}_legacy_window_minutes"] = legacy.catchup_minutes if not schedule.get("end_time") else 0
    for target in selected.targets:
        entries[target] = selected.normalized_destinations[target]
    updated["destinations"] = list(entries.values())
    updated["destinations_migrated"] = True
    value = updated.get("target_formats")
    if isinstance(value, (dict, list)):
        updated["target_formats"] = json.dumps(value, ensure_ascii=False, indent=2)
    return updated


@dataclass
class Settings:
    destinations: list | None = None
    destinations_migrated: bool = False
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
        self.target_schedules = {}
        self.normalized_destinations = {}
        if type(self.destinations_migrated) is not bool:
            raise ValueError("destinations_migrated 必须为布尔值")
        if self.destinations is not None or self.destinations_migrated:
            entries = self.destinations if self.destinations is not None else []
            if not isinstance(entries, list):
                raise ValueError("发送目标配置必须为条目列表")
            mappings = {}
            for index, entry in enumerate(entries, 1):
                schedule_keys = {f"{k}_{f}" for k in ("daily", "history") for f in ("send_time", "retry_minutes", "end_time", "legacy_window_minutes")}
                if not isinstance(entry, dict) or set(entry) - ({"target", "daily", "history", "__template_key"} | schedule_keys):
                    raise ValueError(f"第 {index} 项目标配置字段无效")
                if entry.get("__template_key", "target") != "target":
                    raise ValueError(f"第 {index} 项使用未知模板")
                target = entry.get("target")
                parts = target.split(":", 2) if isinstance(target, str) else []
                if (len(parts) != 3 or any(not p.strip() or p != p.strip() for p in parts)
                        or target != target.strip() or parts[1] not in ("GroupMessage", "FriendMessage", "OtherMessage")):
                    raise ValueError(f"第 {index} 项 UMO 无效：请选择完整会话，消息类型须为 GroupMessage/FriendMessage/OtherMessage")
                if target in mappings:
                    raise ValueError(f"第 {index} 项 UMO 重复，每个目标只能配置一次")
                mappings[target] = {"daily": entry.get("daily", "image"), "history": entry.get("history", "text")}
                normalized = {"__template_key": "target", "target": target, **mappings[target]}
                self.target_schedules[target] = {}
                for kind in ("daily", "history"):
                    schedule = {key: entry.get(f"{kind}_{key}", default) for key, default in
                                (("send_time", "09:00"), ("retry_minutes", 15), ("end_time", "11:00"), ("legacy_window_minutes", 0))}
                    legacy_window = schedule["legacy_window_minutes"]
                    if type(legacy_window) is not int or not 0 <= legacy_window <= 1440:
                        raise ValueError("旧补发窗口须为 0–1440 分钟")
                    if not schedule["send_time"] or (not schedule["end_time"] and not legacy_window):
                        raise ValueError(f"第 {index} 项 {kind} 请填写开始和结束时间")
                    self.target_schedules[target][kind] = schedule
                    normalized.update({f"{kind}_{key}": value for key, value in schedule.items()})
                self.normalized_destinations[target] = normalized
            self.targets = tuple(mappings)
            self.target_formats = mappings
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
                allowed = ("text", "json", "markdown", "image", "off") if kind == "daily" else ("text", "json", "markdown", "off")
                if fmt not in allowed:
                    raise ValueError(f"{kind} 不支持发送格式 {fmt}")
        if not isinstance(self.schedules, dict) or set(self.schedules) - {"daily", "history"}:
            raise ValueError("schedules 仅支持 daily/history")
        all_schedules = list(self.schedules.items()) + [(kind, schedule) for schedules in self.target_schedules.values() for kind, schedule in schedules.items()]
        for kind, schedule in all_schedules:
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

    def retry_seconds(self, kind, target=None):
        schedules = self.target_schedules[target] if target is not None else self.schedules
        return schedules.get(kind, {}).get("retry_minutes", 15) * 60

    def window(self, kind, now, target=None):
        """Return schedule-day and inclusive end; old configs retain old boundaries."""
        local = now.astimezone(self.zone)
        schedules = self.target_schedules[target] if target is not None else self.schedules
        schedule = schedules.get(kind, {})
        start = schedule.get("send_time") or self.send_time
        end = schedule.get("end_time")
        hour, minute = map(int, start.split(":"))
        if not end:
            # Compatibility: original catchup window stops at midnight and excludes end.
            begin = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
            midnight = datetime.combine(local.date() + timedelta(days=1), datetime.min.time(), self.zone)
            catchup = schedule.get("legacy_window_minutes") or self.catchup_minutes
            deadline = min(begin.timestamp() + catchup * 60, midnight.timestamp())
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


class ApiPayload(dict):
    """Parsed data for internal decisions plus the unchanged JSON response for delivery."""
    def __init__(self, data, raw_text):
        super().__init__(data)
        self.raw_text = raw_text


class HttpClient:
    def __init__(self, settings, session):
        self.settings, self.session = settings, session

    async def fetch(self, kind):
        return await self.fetch_path("60s" if kind == "daily" else "today-in-history")

    async def fetch_raw(self, kind, fmt):
        return await self.fetch_raw_path("60s" if kind == "daily" else "today-in-history", fmt)

    async def fetch_path(self, endpoint):
        raw = await self.fetch_raw_path(endpoint, "json")
        return ApiPayload(json.loads(raw), raw)

    async def fetch_raw_path(self, endpoint, fmt):
        if fmt not in ("text", "json", "markdown"):
            raise ApiError("文字格式必须为 text/json/markdown")
        path, _, query = endpoint.partition("?")
        params = dict(parse_qsl(query))
        params["encoding"] = fmt
        async with self.session.get(f"{self.settings.base_url.rstrip('/')}/v2/{path}", params=params, allow_redirects=False) as response:
            if response.status == 429:
                raise ApiError("HTTP 429 限流", retry_delay(response.headers.get("Retry-After")))
            if response.status != 200:
                raise ApiError(f"HTTP {response.status}")
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            allowed = ("application/json", "text/json", "", "text/plain") if fmt == "json" else ("text/plain", "text/markdown", "text/x-markdown", "application/json")
            if mime not in allowed:
                raise ApiError("接口没有返回所请求的文字格式，不能作为文字发送")
            raw = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                raw.extend(chunk)
                if len(raw) > 1_000_000:
                    raise ApiError("响应超过 1MB，停止发送（不截断原文）")
            try:
                text = raw.decode("utf-8")
            except UnicodeError as exc:
                raise ApiError("响应不是有效 UTF-8 文字") from exc
            if not text.strip():
                raise ApiError("接口返回空文字")
            if fmt == "json" or mime == "application/json" or text.lstrip().startswith("{"):
                try:
                    data = json.loads(text)
                except ValueError as exc:
                    if fmt == "json" or mime == "application/json":
                        raise ApiError("响应不是有效 JSON") from exc
                else:
                    if fmt == "json" or mime == "application/json" or (isinstance(data, dict) and "code" in data):
                        if not isinstance(data, dict) or data.get("code") != 200:
                            raise ApiError("API 返回业务错误")
                        if fmt != "json":
                            raise ApiError("API 返回 JSON 而非所请求的文字格式")
            return text


class Engine:
    def __init__(self, settings, store, client, send, log, prepare=None, clock=None, image_query=None):
        self.settings, self.store, self.client = settings, store, client
        self.image_query = image_query
        self.send, self.log, self.prepare, self.clock = send, log, prepare, clock
        state = store.load_schedule_state()
        self.next_fetch = {kind: deadline for kind, deadline, _ in state}
        self.last_error = {kind: error for kind, _, error in state if error}
        self.lock = asyncio.Lock()

    @staticmethod
    def _state_key(kind, target):
        return kind if target is None else json.dumps([target, kind], ensure_ascii=False)

    def _cooldown(self, kind, current, error=None, retry_after=0, target=None):
        key = self._state_key(kind, target)
        deadline = current.timestamp() + max(self.settings.retry_seconds(kind, target), retry_after)
        self.store.save_schedule_state(key, deadline, error)
        self.next_fetch[key] = deadline
        if error:
            self.last_error[key] = error
        else:
            self.last_error.pop(key, None)

    async def _fetch_once(self, kind, now):
        try:
            return await self.client.fetch(kind)
        except ApiError as exc:
            if exc.retry_after:
                deadline = (self.clock or (lambda: now))().timestamp() + exc.retry_after
                key = "api:" + kind
                self.store.save_schedule_state(key, deadline, str(exc))
                self.next_fetch[key] = deadline
            raise

    async def _shared_fetch(self, kind, now):
        key = ("fetch", kind)
        if key not in self.round_tasks:
            self.round_tasks[key] = asyncio.create_task(self._fetch_once(kind, now))
        return await asyncio.shield(self.round_tasks[key])

    async def _image_once(self, day, now):
        try:
            return await self.image_query(day, lambda: self._shared_fetch("daily", now))
        except ApiError as exc:
            if exc.retry_after:
                deadline = (self.clock or (lambda: now))().timestamp() + exc.retry_after
                self.store.save_schedule_state("api:daily", deadline, str(exc))
                self.next_fetch["api:daily"] = deadline
            raise

    async def _shared_image(self, day, now):
        key = ("image", day)
        if key not in self.round_tasks:
            self.round_tasks[key] = asyncio.create_task(self._image_once(day, now))
        return await asyncio.shield(self.round_tasks[key])

    async def _prepare_once(self, kind, payload, source, text, target, now, limit_key):
        try:
            return await self.prepare(kind, payload, source, text, target)
        except ApiError as exc:
            if exc.retry_after:
                deadline = (self.clock or (lambda: now))().timestamp() + exc.retry_after
                self.store.save_schedule_state(limit_key, deadline, str(exc))
                self.next_fetch[limit_key] = deadline
            raise

    async def _shared_prepare(self, kind, payload, source, text, target, now):
        fmt = self.settings.format_for(kind, target)
        limit_key = "format:" + kind + ":" + fmt
        remaining = self.next_fetch.get(limit_key, 0) - now.timestamp()
        if remaining > 0:
            raise ApiError("该格式仍在 Retry-After 等待期内", remaining)
        key = ("prepare", kind, source, text, fmt)
        if key not in self.round_tasks:
            self.round_tasks[key] = asyncio.create_task(self._prepare_once(kind, payload, source, text, target, now, limit_key))
        return await asyncio.shield(self.round_tasks[key])

    async def tick(self, now):
        async with self.lock:
            s = self.settings
            if not s.enabled or not s.targets:
                return
            self.round_tasks = {}
            tasks = []
            scopes = s.targets if s.target_schedules or self.image_query is not None else (None,)
            for target in scopes:
                for kind in ("daily", "history"):
                    enabled = (s.format_for(kind, target) != "off" and (bool(s.target_schedules) or getattr(s, kind + "_enabled"))) if target is not None else getattr(s, kind + "_enabled")
                    if not enabled:
                        continue
                    window = s.window(kind, now, target)
                    if window:
                        tasks.append(asyncio.create_task(self._deliver_kind(kind, window, now, target)))
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
            finally:
                for task in self.round_tasks.values():
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*self.round_tasks.values(), return_exceptions=True)

    async def _deliver_kind(self, kind, window, now, scope_target=None):
        s = self.settings
        day, end = window
        current = self.clock or (lambda: now)
        targets = (scope_target,) if scope_target is not None else s.targets
        key = self._state_key(kind, scope_target)
        pending = [t for t in targets if self.store.eligible(day, t, kind)]
        # Retain legacy per-interface cooldowns during the first upgrade as well.
        deadline = max(self.next_fetch.get(key, 0), self.next_fetch.get(kind, 0), self.next_fetch.get("api:" + kind, 0))
        if not pending or current().timestamp() > end or current().timestamp() < deadline:
            return
        # Persist a retry reservation before I/O so restart doesn't cause a tight loop.
        self._cooldown(kind, current(), target=scope_target)
        image_mode = (kind == "daily" and self.image_query is not None
                      and s.format_for(kind, scope_target) == "image")
        try:
            if image_mode:
                remaining = self.next_fetch.get("format:daily:image", 0) - current().timestamp()
                if remaining > 0:
                    raise ApiError("图片仍在 Retry-After 等待期内", remaining)
                result = await asyncio.wait_for(self._shared_image(day, now), s.timeout_seconds)
                source, payload, text = result.source, result.output, ""
                if source != day.isoformat() or source != current().astimezone(ZoneInfo(s.timezone)).date().isoformat():
                    raise ApiError("图片日期不是当天计划日期，等待后重试")
            else:
                payload = await asyncio.wait_for(self._shared_fetch(kind, now), s.timeout_seconds)
                source, text = render(kind, payload, day)
        except Exception as exc:
            self._cooldown(kind, current(), str(exc), getattr(exc, "retry_after", 0), scope_target)
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
                    prepared[fmt] = payload if image_mode else (
                        await asyncio.wait_for(self._shared_prepare(kind, payload, source, text, target, current()), s.timeout_seconds)
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
            if image_mode and source != current().astimezone(ZoneInfo(s.timezone)).date().isoformat():
                error = "取图期间跨日，等待下一计划"
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
        if any(self.store.eligible(day, target, kind) for target in targets):
            self._cooldown(kind, current(), error, format_retry_after, scope_target)
        else:
            self.store.save_schedule_state(key, 0, None)
            self.next_fetch.pop(key, None)
            self.last_error.pop(key, None)
