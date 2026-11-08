"""Delivery formats are independent from upstream API encoding."""
import json
from dataclasses import dataclass
from urllib.parse import urlsplit

from .core import ApiError


@dataclass(frozen=True)
class Output:
    text: str
    image: bytes | None = None


def check_format(value, image_supported=False):
    if value not in ("text", "json", "markdown", "image"):
        raise ApiError("发送格式无效，请选择 text/json/markdown")
    if value == "image" and not image_supported:
        raise ApiError("该接口没有官方整榜图片，不支持 image；请使用 text/json/markdown")
    return value


def structured_output(title, rows, fmt, extra=None):
    check_format(fmt)
    document = {"title": title, **(extra or {}), "items": rows}
    if fmt == "json":
        # Keep JSON valid when limiting output size.
        while len(json.dumps(document, ensure_ascii=False)) > 3500 and document["items"]:
            document["items"] = document["items"][:-1]
            document["truncated"] = True
        return Output(json.dumps(document, ensure_ascii=False))
    prefix = "## " if fmt == "markdown" else ""
    lines = [prefix + title]
    if extra:
        lines.extend(f"{k}: {v}" for k, v in extra.items())
    for index, item in enumerate(rows, 1):
        title = str(item.get("title", ""))
        details = " | ".join(f"{k}: {v}" for k, v in item.items() if k not in ("title", "link"))
        block = f"{index}. {title}" + (f"\n{details}" if details else "")
        if item.get("link"):
            block += f"\n{item['link']}"
        if sum(map(len, lines)) + len(block) > 3400:
            lines.append("（长度限制，后续条目已省略）")
            break
        lines.append(block)
    return Output("\n".join(lines))


async def daily_output(kind, payload, source, text, fmt, image_loader):
    check_format(fmt, image_supported=kind == "daily")
    if fmt == "image":
        url = payload["data"].get("image")
        if not isinstance(url, str) or not url:
            raise ApiError("日报没有返回 image 图片地址，不能发送图片")
        image = await image_loader(url)
        return Output(f"每天60秒读懂世界｜接口日期：{source}\n来源：60s API / 原日报发布方", image)
    if fmt == "json":
        return Output(json.dumps({"source_date": source, "content": text[:3000]}, ensure_ascii=False))
    return Output(("## " if fmt == "markdown" else "") + text)


async def load_image(session, url, allowed_hosts):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts or parsed.username or parsed.port not in (None, 443):
        raise ApiError("日报图片地址不在允许的 HTTPS 图片域名列表中")
    async with session.get(url, allow_redirects=False) as response:
        if response.status != 200:
            raise ApiError(f"图片 HTTP {response.status}")
        mime = response.headers.get("Content-Type", "").split(";")[0].lower()
        if mime not in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            raise ApiError("图片接口没有返回支持的图片 MIME 类型")
        raw = bytearray()
        async for chunk in response.content.iter_chunked(65536):
            raw.extend(chunk)
            if len(raw) > 5_000_000:
                raise ApiError("图片超过 5MB，已停止下载")
        signatures = {
            "image/png": raw.startswith(b'\x89PNG\r\n\x1a\n'),
            "image/jpeg": raw.startswith(b'\xff\xd8\xff'),
            "image/webp": raw.startswith(b'RIFF') and raw[8:12] == b'WEBP',
            "image/gif": raw.startswith((b'GIF87a', b'GIF89a')),
        }
        if not signatures[mime]:
            raise ApiError("图片字节与 MIME 类型不符，不发送伪图片")
        return bytes(raw)
