"""ParserService 主入口（任务 13）。

职责：
- 接收文件字节 + mime
- 按 mime 路由到 PDF / DOCX / OCR
- 返回 ``ParsedResult``（含 text + status + error）

状态机：
- ``text >= PARSE_MIN_TEXT_LENGTH`` → status="success"
- ``0 < text < PARSE_MIN_TEXT_LENGTH`` → status="low_text"
- 解析异常 → status="failed"（保留 error 摘要）

注意：本服务只产文本 + 状态；写库由 celery task 负责（避免服务层耦合 DB）。
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Literal

from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.logging import get_logger
from app.services.parser.docx_parser import DOCXParseError, parse_docx
from app.services.parser.ocr import OCRAdapter, get_ocr_adapter
from app.services.parser.pdf_parser import PDFParseError, parse_pdf

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


LOW_TEXT_MIN_LENGTH: int = settings.PARSE_MIN_TEXT_LENGTH
"""文本长度低于此值 → low_text 状态（默认 50）。"""


_ParseStatus = Literal["success", "low_text", "failed"]


# ============================================================================
# 异常
# ============================================================================


class ParserError(Exception):
    """Parser 顶层错误（路由失败 / 不支持的 mime 等）。"""


# ============================================================================
# 结果
# ============================================================================


@dataclass(frozen=True)
class ParsedResult:
    """解析结果。"""

    text: str
    status: _ParseStatus
    mime: str
    error: str | None = None
    ocr_backend: str | None = None  # 实际用到的 OCR backend（paddle / stub / None）
    page_count: int | None = None  # PDF 页数（仅 PDF）

    @property
    def is_terminal_failure(self) -> bool:
        return self.status == "failed"


# ============================================================================
# 路由
# ============================================================================


_MIME_PDF = "application/pdf"
_MIME_DOCX = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
_MIME_DOC = "application/msword"
_MIME_PNG = "image/png"
_MIME_JPEG = "image/jpeg"

_IMAGE_MIMES = {_MIME_PNG, _MIME_JPEG, "image/jpg"}


class ParserService:
    """按 MIME 路由的解析服务。"""

    def __init__(
        self,
        *,
        ocr: OCRAdapter | None = None,
        pdf_rasterizer=None,
    ) -> None:
        self._ocr = ocr or get_ocr_adapter()
        self._pdf_rasterizer = pdf_rasterizer

    async def parse(self, content: bytes, *, mime: str) -> ParsedResult:
        """主入口：按 mime 路由 → 解析 → 标状态。

        Args:
            content: 文件字节
            mime: 标准 MIME，如 ``application/pdf``

        Returns:
            ParsedResult（status ∈ success / low_text / failed）

        任何子解析器抛异常都包装为 status=failed；不抛到调用方。
        """
        if not content:
            return ParsedResult(
                text="",
                status="failed",
                mime=mime,
                error="empty file content",
            )

        try:
            text = await self._dispatch(content, mime)
        except (PDFParseError, DOCXParseError) as exc:
            logger.warning(
                "parser_failed",
                mime=mime,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ParsedResult(
                text="",
                status="failed",
                mime=mime,
                error=f"{type(exc).__name__}: {exc}"[:500],
                ocr_backend=self._ocr.backend_name,
            )
        except UnidentifiedImageError as exc:
            return ParsedResult(
                text="",
                status="failed",
                mime=mime,
                error=f"image_unidentified: {exc}"[:500],
                ocr_backend=self._ocr.backend_name,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("parser_unexpected_error", mime=mime)
            return ParsedResult(
                text="",
                status="failed",
                mime=mime,
                error=f"{type(exc).__name__}: {exc}"[:500],
                ocr_backend=self._ocr.backend_name,
            )

        status = self._classify(text)
        return ParsedResult(
            text=text,
            status=status,
            mime=mime,
            ocr_backend=self._ocr.backend_name,
        )

    # ----- 路由分发 -----

    async def _dispatch(self, content: bytes, mime: str) -> str:
        if mime == _MIME_PDF:
            return await parse_pdf(
                content,
                ocr=self._ocr,
                rasterizer=self._pdf_rasterizer,
            )

        if mime == _MIME_DOCX:
            return parse_docx(content)

        if mime == _MIME_DOC:
            # .doc 老格式（OLE2）暂不支持，直接抛
            raise DOCXParseError(
                "legacy .doc format not supported; convert to .docx or upload PDF"
            )

        if mime in _IMAGE_MIMES:
            return await self._ocr_image(content)

        raise ParserError(f"unsupported mime: {mime}")

    async def _ocr_image(self, content: bytes) -> str:
        # 用 PIL 先验证是合法图片，再送 OCR
        with Image.open(io.BytesIO(content)) as img:
            img.verify()  # 抛 UnidentifiedImageError if invalid
        return await self._ocr.extract(content, langs=("ch", "en"))

    # ----- 状态分类 -----

    @staticmethod
    def _classify(text: str) -> _ParseStatus:
        stripped = text.strip()
        if not stripped:
            return "failed"
        if len(stripped) < LOW_TEXT_MIN_LENGTH:
            return "low_text"
        return "success"


__all__ = [
    "ParserService",
    "ParserError",
    "ParsedResult",
    "LOW_TEXT_MIN_LENGTH",
]
