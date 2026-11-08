"""Delivery formats are independent from upstream API encoding."""
import json
from dataclasses import dataclass

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
        raise ApiError("图片请使用 image 日期查询流程")
    if fmt == "json":
        return Output(json.dumps({"source_date": source, "content": text[:3000]}, ensure_ascii=False))
    return Output(("## " if fmt == "markdown" else "") + text)

