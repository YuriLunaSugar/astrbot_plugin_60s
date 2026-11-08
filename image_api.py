"""Image first, filename date first, JSON date only when the filename is unknown."""
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from .core import ApiError, retry_delay
from .formats import Output


def calendar_date(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def filename_date(url):
    try:
        filename = urlsplit(url).path.rsplit('/', 1)[-1]
    except ValueError:
        return None
    match = re.fullmatch(r'([0-9]{4}-[0-9]{2}-[0-9]{2})\.(?:png|jpe?g|webp|gif)', filename, re.IGNORECASE)
    return calendar_date(match.group(1)) if match else None


@dataclass(frozen=True)
class ImageResult:
    source: str | None
    output: Output


class ImageService:
    def __init__(self, settings, session, json_fetch, clock=None):
        self.settings, self.session, self.json_fetch = settings, session, json_fetch
        self.clock = clock or (lambda: datetime.now(UTC))

    def check_date(self, source, expected):
        if expected is None:
            return
        today = self.clock().astimezone(ZoneInfo(self.settings.timezone)).date()
        if source != expected.isoformat() or source != today.isoformat():
            raise ApiError(f'日报日期 {source or "无法确认"}，当前日期 {today}、计划日期 {expected}；等待后重试')

    @staticmethod
    def check_status(response):
        if response.status == 429:
            raise ApiError('图片 HTTP 429 限流', retry_delay(response.headers.get('Retry-After')))
        if response.status != 200:
            raise ApiError(f'图片 HTTP {response.status}')

    @staticmethod
    async def read_bytes(response):
        raw = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            raw.extend(chunk)
        return bytes(raw)

    async def query(self, expected=None):
        api = self.settings.base_url.rstrip('/') + '/v2/60s'
        target, source, raw = None, None, None
        async with self.session.get(api, params={'encoding': 'image'}, allow_redirects=False) as response:
            if response.status in (301, 302, 303, 307, 308):
                location = response.headers.get('Location')
                if not location:
                    raise ApiError('图片重定向缺少 Location')
                # Resolve relative Location against the API response URL; never parse query dates.
                target = urljoin(str(getattr(response, 'url', api)), location)
                source = filename_date(target)
            else:
                self.check_status(response)
                raw = await self.read_bytes(response)
        basis = '图片文件名'
        if source is None:
            basis = 'JSON 回退'
            payload = await self.json_fetch()
            data = payload.get('data') if isinstance(payload, dict) and payload.get('code') == 200 else None
            source = calendar_date(data.get('date')) if isinstance(data, dict) else None
        self.check_date(source, expected)
        if target is not None:
            async with self.session.get(target, allow_redirects=True) as response:
                self.check_status(response)
                raw = await self.read_bytes(response)
                # If further redirects expose a different dated filename, prefer that final date.
                final_date = filename_date(str(getattr(response, 'url', '')))
                if final_date is not None and final_date != source:
                    source, basis = final_date, '最终图片文件名'
        self.check_date(source, expected)
        today = self.clock().astimezone(ZoneInfo(self.settings.timezone)).date().isoformat()
        if source is None:
            notice = '日期无法确认，不能视为今日日报。'
        else:
            notice = f'日期：{source}（{basis}）'
            if source != today:
                notice += f'；非今日内容，当前日期 {today}。'
        if basis == 'JSON 回退':
            notice += '\nJSON 与图片非原子读取，不能确认图片内容日期一致。'
        return ImageResult(source, Output('每天60秒读懂世界｜' + notice + '\n来源：60s API / 原日报发布方', raw))
