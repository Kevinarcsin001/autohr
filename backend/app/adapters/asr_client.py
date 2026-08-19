"""ASR 微服务客户端 — 调用 asr 容器(faster-whisper)转写面试音频。

与 ``adapters/mineru_parser.py`` 同构：HTTP 直连容器、超时显式、错误转译。
"""
from __future__ import annotations

import httpx

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class ASRError(Exception):
    """转写失败。"""


class ASRClient:
    """薄客户端：multipart 上传音频 → {text, segments, language, duration}。"""

    def __init__(self, base_url: str | None = None, timeout: float | None = None) -> None:
        self._base_url = (base_url or settings.ASR_BASE_URL).rstrip("/")
        self._timeout = timeout or settings.ASR_TIMEOUT_SECONDS

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str = "audio.webm",
        initial_prompt: str | None = None,
    ) -> dict:
        """转写音频。initial_prompt 注入职位/技能领域词提升专名准确率。

        Returns:
            {text, segments: [{start, end, text}], language, duration, model}
        """
        files = {"audio": (filename, audio_bytes)}
        data = {}
        if initial_prompt:
            data["initial_prompt"] = initial_prompt[:500]
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/transcribe", files=files, data=data
                )
        except httpx.HTTPError as exc:
            raise ASRError(f"ASR 服务不可达: {exc}") from exc
        if resp.status_code != 200:
            raise ASRError(f"ASR 转写失败({resp.status_code}): {resp.text[:200]}")
        return resp.json()

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base_url}/health")
                return resp.json() if resp.status_code == 200 else {"status": "down"}
        except httpx.HTTPError:
            return {"status": "down"}


__all__ = ["ASRClient", "ASRError"]
