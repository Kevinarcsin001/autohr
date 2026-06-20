"""MinerU PDF 解析适配器 — 通过 HTTP API 调用 MinerU 解析服务。"""
from __future__ import annotations

from app.adapters.parser_base import ParserResult
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class MinerUParserAdapter:
    """MinerU PDF 解析器适配器。

    调用独立的 MinerU HTTP 服务解析 PDF，返回提取的文本。
    服务地址通过 MINERU_ENDPOINT 配置，默认 http://mineru:8001。
    """

    name = "mineru"

    def __init__(self, endpoint: str | None = None):
        self.endpoint = endpoint or getattr(settings, "MINERU_ENDPOINT", "http://mineru:8001")

    async def parse(self, file_bytes: bytes, filename: str = "resume.pdf") -> ParserResult:
        """调用 MinerU API 解析 PDF 文件。"""
        import aiohttp

        url = f"{self.endpoint}/parse"
        form = aiohttp.FormData()
        form.add_field("file", file_bytes, filename=filename, content_type="application/pdf")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=form, timeout=aiohttp.ClientTimeout(total=120)) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.warning("mineru_parse_error", status=resp.status, error=error_text[:200])
                        return ParserResult(status="failed", text="", error=f"MinerU HTTP {resp.status}")

                    data = await resp.json()
                    if data.get("status") == "success":
                        text = data.get("text", "")
                        return ParserResult(
                            status="success" if len(text) >= 50 else "low_text",
                            text=text,
                            pages=data.get("pages", 1),
                        )
                    else:
                        return ParserResult(
                            status="failed",
                            text="",
                            error=data.get("error", "MinerU parse failed"),
                        )
        except Exception as e:
            logger.warning("mineru_unavailable", error=str(e))
            return ParserResult(status="failed", text="", error=f"MinerU unavailable: {e}")

    async def health_check(self) -> bool:
        """检查 MinerU 服务是否可用。"""
        import aiohttp
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{self.endpoint}/health", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    return resp.status == 200
        except Exception:
            return False
