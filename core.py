"""Framework-independent daily delivery engine; all external I/O is injectable."""
import asyncio
import json
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlsplit
from zoneinfo import ZoneInfo


class ApiError(Exception):
    def __init__(self, message, retry_after=900):
        super().__init__(message)
        self.retry_after = retry_after


@dataclass
class Settings:
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
        self.db.commit()

    def has(self, day, target, kind):
        return self.db.execute("SELECT 1 FROM deliveries WHERE day=? AND target=? AND kind=?", (str(day), target, kind)).fetchone() is not None

    def claim(self, day, target, kind, source):
        with self.db:
            return self.db.execute("INSERT OR IGNORE INTO deliveries VALUES (?,?,?,?,?)", (str(day), target, kind, source, "pending")).rowcount == 1

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
    def __init__(self, settings, store, client, send, log, prepare=None):
        self.settings, self.store, self.client = settings, store, client
        self.send, self.log = send, log
        self.prepare = prepare
        self.cache = {}
        self.next_fetch = 0
        self.lock = asyncio.Lock()

    async def tick(self, now):
        async with self.lock:
            s = self.settings
            day = s.due_day(now)
            if not s.enabled or not s.targets or day is None:
                return
            self.cache = {k: v for k, v in self.cache.items() if k[0] == day}
            for kind, enabled in (("daily", s.daily_enabled), ("history", s.history_enabled)):
                pending = [t for t in s.targets if not self.store.has(day, t, kind)]
                if not enabled or not pending:
                    continue
                key = (day, kind)
                if key not in self.cache:
                    if now.timestamp() < self.next_fetch:
                        continue
                    try:
                        payload = await asyncio.wait_for(self.client.fetch(kind), s.timeout_seconds)
                        source, text = render(kind, payload, day)
                        if self.prepare is not None:
                            text = await asyncio.wait_for(self.prepare(kind, payload, source, text), s.timeout_seconds)
                        self.cache[key] = source, text
                    except Exception as exc:
                        self.next_fetch = now.timestamp() + max(900, getattr(exc, "retry_after", 900))
                        self.log(f"{kind} 获取失败: {exc}")
                        continue
                source, text = self.cache[key]
                for target in pending:
                    if not self.store.claim(day, target, kind, source):
                        continue
                    try:
                        result = await asyncio.wait_for(self.send(target, text), s.timeout_seconds)
                        if result is False:
                            raise RuntimeError("适配器返回 False")
                    except Exception as exc:
                        self.store.finish(day, target, kind, "failed_or_unknown")
                        self.log(f"{kind} 发送失败或结果未知，不自动重发: {type(exc).__name__}")
                    else:
                        self.store.finish(day, target, kind, "sent")
