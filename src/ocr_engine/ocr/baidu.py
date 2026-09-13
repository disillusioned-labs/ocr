"""Baidu AI Studio adapter - the only place that knows the Baidu wire shapes.

Reference (observed, binding): docs/reference/api-ocr-baidu.md. Both the sync
and async surfaces carry the same prunedResult.overall_ocr_res arrays; the
markdown blob is never read.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Protocol

import httpx

from ..obs.logging import get_logger
from ..obs.tracing import tracer
from ..settings import BAIDU_ASYNC_ERROR_PERMANENT, BAIDU_RETRYABLE_HTTP, Settings
from .base import Line

log = get_logger(__name__)


class ProviderPermanentError(Exception):
    """The input or credential is wrong; retrying cannot help."""


class ProviderTransientError(Exception):
    """Rate limit, 5xx, timeout, or network failure - SAQ should retry."""


class AsyncClientFactory(Protocol):
    def __call__(self) -> httpx.AsyncClient: ...


class BaiduProvider:
    def __init__(self, settings: Settings, client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._client = client

    async def extract_lines(self, content: bytes, mime: str) -> list[Line]:
        with tracer("BaiduProvider.extract_lines").start_as_current_span("extract_lines") as span:
            span.set_attribute("ocr.baidu.mode", self._settings.baidu_mode.value)
            span.set_attribute("ocr.baidu.mime", mime)
            try:
                if self._settings.baidu_mode.value == "async":
                    raw = await self._extract_async(content, mime)
                else:
                    raw = await self._extract_sync(content, mime)
            except ProviderPermanentError:
                raise
            except (ProviderTransientError, httpx.HTTPError, TimeoutError) as exc:
                raise ProviderTransientError(str(exc)) from exc
            return normalize_layout_parsing(raw)

    async def _extract_sync(self, content: bytes, mime: str) -> dict:
        payload = {
            "file": base64.b64encode(content).decode(),
            "fileType": 0 if mime == "application/pdf" else 1,
            "useDocOrientationClassify": self._settings.baidu_unwarp,
            "useDocUnwarping": self._settings.baidu_unwarp,
            "visualize": False,
        }
        response = await self._client.post(
            self._settings.baidu_api_url,
            json=payload,
            headers=self._sync_headers(),
            timeout=self._settings.baidu_timeout,
        )
        if response.status_code in BAIDU_RETRYABLE_HTTP:
            raise ProviderTransientError(f"baidu sync {response.status_code}")
        if response.status_code != 200:
            raise ProviderPermanentError(f"baidu sync {response.status_code}: {response.text[:200]}")
        body = response.json()
        if body.get("errorCode") not in (0, None):
            raise ProviderPermanentError(
                f"baidu sync errorCode={body.get('errorCode')}: {body.get('errorMsg')}"
            )
        return body

    async def _extract_async(self, content: bytes, mime: str) -> dict:
        job_id = await self._submit_async_job(content, mime)
        json_url = await self._poll_async_job(job_id)
        response = await self._client.get(json_url)
        response.raise_for_status()
        first_line = response.text.strip().splitlines()[0]
        return json.loads(first_line)

    async def _submit_async_job(self, content: bytes, mime: str) -> str:
        files = {"file": ("document", content, mime)}
        data = {
            "model": "PP-StructureV3",
            "optionalPayload": json.dumps(
                {
                    "useDocOrientationClassify": self._settings.baidu_unwarp,
                    "useDocUnwarping": self._settings.baidu_unwarp,
                }
            ),
        }
        response = await self._client.post(
            f"{self._settings.baidu_async_url}/api/v2/ocr/jobs",
            files=files,
            data=data,
            headers={"Authorization": f"Bearer {self._settings.baidu_token.get_secret_value()}"},
        )
        body = _safe_json(response)
        code = body.get("code")
        if code == 0:
            return str(body["data"]["jobId"])
        if code in BAIDU_ASYNC_ERROR_PERMANENT or response.status_code in (401, 413):
            raise ProviderPermanentError(f"baidu async submit code={code}: {body.get('msg')}")
        raise ProviderTransientError(f"baidu async submit code={code} http={response.status_code}")

    async def _poll_async_job(self, job_id: str) -> str:
        url = f"{self._settings.baidu_async_url}/api/v2/ocr/jobs/{job_id}"
        headers = {"Authorization": f"Bearer {self._settings.baidu_token.get_secret_value()}"}
        deadline = asyncio.get_running_loop().time() + self._settings.baidu_poll_timeout
        while True:
            response = await self._client.get(url, headers=headers)
            body = _safe_json(response)
            state = (body.get("data") or {}).get("state")
            if state == "done":
                return body["data"]["resultUrl"]["jsonUrl"]
            if state == "failed":
                detail = (body.get("data") or {}).get("errorMsg", "")
                raise ProviderPermanentError(f"baidu async job failed: {detail}")
            if asyncio.get_running_loop().time() > deadline:
                raise ProviderTransientError(f"baidu async job {job_id} poll timeout")
            await asyncio.sleep(self._settings.baidu_poll_interval)

    def _sync_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {self._settings.baidu_token.get_secret_value()}",
            "Content-Type": "application/json",
        }


def normalize_layout_parsing(body: dict) -> list[Line]:
    """overall_ocr_res -> Line; the only normalization point for Baidu output."""
    lines: list[Line] = []
    for page_index, page in enumerate(body.get("result", {}).get("layoutParsingResults", []), start=1):
        ocr = ((page.get("prunedResult") or {}).get("overall_ocr_res")) or {}
        texts = ocr.get("rec_texts") or []
        scores = ocr.get("rec_scores") or []
        boxes = ocr.get("rec_boxes") or []
        for text, score, box in zip(texts, scores, boxes, strict=False):
            cleaned = str(text).strip()
            if not cleaned or score <= 0.0:
                continue
            if len(cleaned) == 1 and not cleaned.isascii():
                continue  # stray CJK/full-width glyphs leak from the VL decoder
            x1, y1, x2, y2 = (int(v) for v in list(box)[:4]) if len(list(box)) >= 4 else (0, 0, 0, 0)
            lines.append(Line(text=cleaned, score=float(score), bbox=(x1, y1, x2, y2), page=page_index))
    return lines


def _safe_json(response: httpx.Response) -> dict:
    try:
        return response.json()
    except ValueError as exc:
        raise ProviderTransientError(f"baidu non-JSON response {response.status_code}") from exc
