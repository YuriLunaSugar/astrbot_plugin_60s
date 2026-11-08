"""Delivery formats are independent from upstream API encoding."""
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

