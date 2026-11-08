"""One-shot daily queries; no scheduler, delivery store, or result cache."""
import asyncio
import json
import math
import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from .core import ApiError, render
from .formats import Output, check_format, daily_output


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
                          '图片先请求 image，文件名无日期再请求 JSON（1 或 2 次 API 查询），随后下载图片；无法确认日期会提示。其他格式查询一次；不自动重试。'
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
            payload = await asyncio.wait_for(self.fetch(kind), self.timeout)
        except ApiError as exc:
            if exc.retry_after:
                self.api_until[kind] = self.clock().timestamp() + exc.retry_after
                raise ApiError(f'{exc}；至少等待 {math.ceil(exc.retry_after)} 秒，本次不重试', exc.retry_after) from exc
            raise
        if not isinstance(payload, dict) or payload.get('code') != 200 or not isinstance(payload.get('data'), dict):
            raise ApiError('API 业务错误或数据格式无效')
        source = payload['data'].get('date')
        if not isinstance(source, str):
            raise ApiError('API 缺少真实日期')
        today = self.clock().astimezone(self.zone).date()
        try:
            if kind == 'daily':
                if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', source):
                    raise ValueError('expected YYYY-MM-DD')
                actual = date.fromisoformat(source)
                differs = actual != today
            else:
                if not re.fullmatch(r'\d{1,2}-\d{1,2}', source):
                    raise ValueError('expected MM-DD')
                month, day = map(int, source.split('-'))
                actual = date(2000, month, day)  # Validate MM-DD, including February 29.
                differs = (month, day) != (today.month, today.day)
        except (ValueError, TypeError) as exc:
            raise ApiError('接口返回日期无效') from exc
        source, text = render(kind, payload, actual)
        try:
            output = await asyncio.wait_for(
                daily_output(kind, payload, source, text, fmt, self.image_loader), self.timeout)
        except ApiError as exc:
            if fmt == 'image' and exc.retry_after:
                self.image_until = self.clock().timestamp() + exc.retry_after
                raise ApiError(f'{exc}；至少等待 {math.ceil(exc.retry_after)} 秒，本次不重试', exc.retry_after) from exc
            raise
        if differs:
            notice = f'接口返回非今日内容：接口日期 {source}，当前日期 {today}（{self.zone.key}）。本次展示实际返回内容。'
            if fmt == 'json':
                doc = json.loads(output.text)
                doc.update(current_date=str(today), notice=notice)
                return Output(json.dumps(doc, ensure_ascii=False))
            return Output(notice + '\n' + output.text, output.image)
        return output
