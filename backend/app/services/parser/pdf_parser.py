"""PDF 解析器（任务 13）。

策略：
1. 优先用 ``pdfplumber`` 提取文本层
2. 计算每页字符密度 = 字符数 / 1（per page）
3. 任一页字符密度 < ``PDF_TEXT_DENSITY_THRESHOLD`` → 视为扫描版 PDF → 回退 OCR
   （将每页 rasterize 成 PNG → 送 OCR）
4. 整文档提取后总字符 < ``PARSE_MIN_TEXT_LENGTH`` → 由 service 层标记 low_text

回退 OCR 的 rasterize 用 pdfplumber 的 ``page.to_image()``（依赖 wand/ImageMagick
或 PyMuPDF）。**生产路径推荐 PyMuPDF (fitz) 直接 rasterize**；测试可注入 fake
rasterizer 跳过。

接口：
- ``parse_pdf(content: bytes, *, ocr: OCRAdapter, rasterizer=None) -> str``
"""
from __future__ import annotations

import io
from typing import Any, Callable

import pdfplumber

from app.core.config import settings
from app.core.logging import get_logger
from app.services.parser.ocr import OCRAdapter

logger = get_logger(__name__)


# 默认 OCR 语言组合（中英文混排）
_DEFAULT_LANGS: tuple[str, ...] = ("ch", "en")


# ============================================================================
# 异常
# ============================================================================


class PDFParseError(Exception):
    """PDF 解析错误（损坏文件 / 无法 rasterize 等）。"""


# ============================================================================
# 主入口
# ============================================================================


Rasterizer = Callable[[bytes, int], bytes]
"""把指定页 rasterize 成 PNG 字节的函数。

Args:
    content: 原始 PDF bytes
    page_index: 0-based 页码
Returns:
    PNG 字节流
"""


async def parse_pdf(
    content: bytes,
    *,
    ocr: OCRAdapter,
    rasterizer: Rasterizer | None = None,
    density_threshold: int | None = None,
    langs: tuple[str, ...] = _DEFAULT_LANGS,
) -> str:
    """提取 PDF 全部页面的文本。

    Args:
        content: PDF 字节流
        ocr: OCR 适配器（密度过低时回退用）
        rasterizer: PDF 页 → PNG 字节的函数；None 时用默认（pdfplumber page.to_image）
        density_threshold: 字符密度阈值（per page）；None 取 settings
        langs: OCR 语言

    Returns:
        全文（``\\n`` 分页）

    Raises:
        PDFParseError: PDF 损坏或读取失败
    """
    if not content:
        raise PDFParseError("empty pdf content")

    threshold = (
        density_threshold
        if density_threshold is not None
        else settings.PDF_TEXT_DENSITY_THRESHOLD
    )

    try:
        text_pages, densities = _extract_text_layer(content)
    except PDFParseError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PDFParseError(f"pdfplumber failed: {exc}") from exc

    if not text_pages:
        # 完全无文本层 → 直接走 OCR
        logger.info("pdf_no_text_layer_fallback_to_ocr", pages=len(densities))
        return await _ocr_all_pages(content, len(densities) or 1, ocr, rasterizer, langs)

    # 任一页密度过低 → 整文档回退 OCR（避免中英混杂只部分页 OCR）
    if any(d < threshold for d in densities):
        logger.info(
            "pdf_low_density_fallback_to_ocr",
            pages=len(densities),
            densities=desities_to_log(densities),
            threshold=threshold,
        )
        return await _ocr_all_pages(content, len(densities), ocr, rasterizer, langs)

    return "\n".join(text_pages)


# ============================================================================
# 内部：文本层提取
# ============================================================================


def _extract_text_layer(content: bytes) -> tuple[list[str], list[int]]:
    """用 pdfplumber 提取每页文本，返回 (页文本列表, 每页字符数列表)。"""
    pages_text: list[str] = []
    densities: list[int] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages:
            try:
                t = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                logger.warning("pdf_page_extract_failed", error=str(exc))
                t = ""
            pages_text.append(t)
            densities.append(len(t.strip()))
    return pages_text, densities


def desities_to_log(densities: list[int]) -> list[int]:
    """防止日志过长（>10 页只取前 10）。"""
    return densities[:10] if len(densities) > 10 else densities


# ============================================================================
# 内部：OCR 回退
# ============================================================================


async def _ocr_all_pages(
    content: bytes,
    page_count: int,
    ocr: OCRAdapter,
    rasterizer: Rasterizer | None,
    langs: tuple[str, ...],
) -> str:
    """对每页 rasterize → OCR → 拼接。"""
    if rasterizer is None:
        rasterizer = _default_rasterize

    out: list[str] = []
    for idx in range(page_count):
        try:
            png_bytes = rasterizer(content, idx)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "pdf_page_rasterize_failed",
                page_index=idx,
                error=str(exc),
            )
            continue
        if not png_bytes:
            continue
        text = await ocr.extract(png_bytes, langs=langs)
        if text:
            out.append(text)

    return "\n".join(out)


def _default_rasterize(content: bytes, page_index: int) -> bytes:
    """默认 rasterizer：用 pdfplumber ``page.to_image().original`` 拿 PNG bytes。

    注意：pdfplumber 的 ``to_image`` 依赖 ``Wand``（ImageMagick binding）或
    ``pdf2image``。**生产推荐装 PyMuPDF 替换本函数**，性能更稳。

    测试环境请注入 fake rasterizer，避免依赖 ImageMagick。
    """
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        if page_index >= len(pdf.pages):
            raise PDFParseError(f"page index {page_index} out of range")
        page = pdf.pages[page_index]
        im = page.to_image(resolution=200)
        # im.original 是 PIL.Image；返回 PNG bytes
        from io import BytesIO

        buf = BytesIO()
        im.original.save(buf, format="PNG")
        return buf.getvalue()


__all__ = [
    "parse_pdf",
    "PDFParseError",
    "Rasterizer",
]
