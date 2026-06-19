"""ParserService 包：把简历文件（PDF/Word/图片）转纯文本（任务 13）。

子模块：
- ``ocr``：OCR 适配器（PaddleOCR 懒加载 + Stub 兜底）
- ``pdf_parser``：PDF 文本层 + 密度阈值回退 OCR
- ``docx_parser``：Word 正文 + 表格
- ``service``：主入口，按 MIME 路由
"""
from __future__ import annotations

from app.services.parser.service import (
    LOW_TEXT_MIN_LENGTH,
    ParsedResult,
    ParserError,
    ParserService,
)

__all__ = [
    "ParserService",
    "ParserError",
    "ParsedResult",
    "LOW_TEXT_MIN_LENGTH",
]
