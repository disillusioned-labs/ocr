"""Provider dispatch - the only place the active provider is chosen."""

from __future__ import annotations

from typing import Protocol

import httpx

from ..settings import Provider, Settings
from .baidu import BaiduProvider
from .base import Line


class OcrProvider(Protocol):
    async def extract_lines(self, content: bytes, mime: str) -> list[Line]: ...


def build_provider(settings: Settings, client: httpx.AsyncClient) -> OcrProvider:
    match settings.provider:
        case Provider.BAIDU_AISTUDIO:
            return BaiduProvider(settings, client)
        case Provider.PADDLEOCR_LOCAL:
            raise NotImplementedError(
                "paddleocr_local is not implemented yet - the model path lands once the "
                "VPS can host the weights; use OCR_PROVIDER=baidu_aistudio"
            )
    raise ValueError(f"unknown provider {settings.provider}")
