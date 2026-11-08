"""One-shot daily queries; no scheduler, delivery store, or result cache."""
import asyncio
import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from .core import ApiError
from .formats import Output, check_format


class ManualService:
    def __init__(self, fetch, image_loader, timezone='Asia/Shanghai', timeout=20,
                 clock=None, limit_reader=None):
        self.fetch, self.image_loader = fetch, image_loader
        self.zone, self.timeout = ZoneInfo(timezone), timeout
        self.clock = clock or (lambda: datetime.now(UTC))
        self.limit_reader = limit_reader or (lambda kind, fmt: 0)
        self.api_until = {}
        self.image_until = 0

    async def query(self, kind, fmt=''):
        if kind not in ('daily', 'history'):
            raise ApiError('未知日更接口')
        fmt = fmt.strip().lower() or ('image' if kind == 'daily' else 'text')
        command = '/60s' if kind == 'daily' else '/history'
        allowed = 'image/text/json/markdown' if kind == 'daily' else 'text/json/markdown'
        if fmt in ('帮助', 'help', '-h', '--help'):
            return Output(f'{command} [格式]\n可选：{allowed}；默认：{"image" if kind == "daily" else "text"}。\n'
                          '每次有效查询只回复当前会话，不改变定时记录。'
                          '图片先请求 image，文件名无日期再请求 JSON（1 或 2 次 API 查询），随后下载图片；成功内容不附加日期或说明。其他格式查询一次；不自动重试。'
                          '仅查询接口当前内容，不支持指定日期或其他发送目标。')
        if fmt not in ('image', 'text', 'json', 'markdown'):
            raise ApiError(f'发送格式无效，请使用 {command} [{allowed}]')
        check_format(fmt, image_supported=kind == 'daily')
        deadline = max(self.api_until.get(kind, 0), self.limit_reader(kind, fmt),
                       self.image_until if fmt == 'image' else 0)
        remaining = math.ceil(deadline - self.clock().timestamp())
        if remaining > 0:
            raise ApiError(f'接口限流等待中，请在 {remaining} 秒后手动重试；本次未请求')
        if fmt == 'image':
            try:
                result = await asyncio.wait_for(self.image_loader(), self.timeout)
            except ApiError as exc:
                if exc.retry_after:
                    self.image_until = self.clock().timestamp() + exc.retry_after
                    raise ApiError(f'{exc}；至少等待 {math.ceil(exc.retry_after)} 秒，本次不重试', exc.retry_after) from exc
                raise
            return result.output
        try:
            text = await asyncio.wait_for(self.fetch(kind, fmt), self.timeout)
        except ApiError as exc:
            if exc.retry_after:
                self.api_until[kind] = self.clock().timestamp() + exc.retry_after
                raise ApiError(f'{exc}；至少等待 {math.ceil(exc.retry_after)} 秒，本次不重试', exc.retry_after) from exc
            raise
        if not isinstance(text, str) or not text.strip():
            raise ApiError('API 没有返回有效文字')
        return Output(text)
