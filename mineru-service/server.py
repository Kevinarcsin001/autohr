"""MinerU PDF 解析服务 — 使用 magic-pdf 命令行工具 + PyMuPDF 兜底。"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import fitz  # PyMuPDF
from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

app = FastAPI(title="MinerU Parser", version="0.2.0")


class ParseResult(BaseModel):
    status: str
    text: str
    pages: int
    error: str | None = None


@app.get("/health")
async def health():
    return {"status": "ok", "service": "mineru-parser"}


def _parse_with_pymupdf(pdf_bytes: bytes) -> tuple[str, int]:
    """使用 PyMuPDF 提取 PDF 文本。"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        text = page.get_text("text")
        if text.strip():
            pages.append(text)
    doc.close()
    return "\n\n".join(pages), len(pages) or 1


def _parse_with_magic_pdf(pdf_path: str, output_dir: str) -> str:
    """使用 magic-pdf CLI 解析 PDF，返回 Markdown 文本。"""
    result = subprocess.run(
        [
            "magic-pdf",
            "-p", pdf_path,
            "-o", output_dir,
            "-m", "auto",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    # 查找生成的 Markdown 文件
    for root, _, files in os.walk(output_dir):
        for f in files:
            if f.endswith(".md"):
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    return fh.read()
    # CLI 失败时返回错误信息
    raise RuntimeError(result.stderr[:500] if result.stderr else "No markdown output")


@app.post("/parse", response_model=ParseResult)
async def parse_pdf(file: UploadFile = File(...)) -> ParseResult:
    """解析 PDF 文件，返回提取的文本。"""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(400, "Only PDF files are supported")

    content = await file.read()
    tmp_dir = tempfile.mkdtemp()
    pdf_path = os.path.join(tmp_dir, file.filename or "input.pdf")

    try:
        with open(pdf_path, "wb") as f:
            f.write(content)

        output_dir = os.path.join(tmp_dir, "output")
        os.makedirs(output_dir, exist_ok=True)

        # 优先使用 magic-pdf CLI, 失败降级到 PyMuPDF
        try:
            text = _parse_with_magic_pdf(pdf_path, output_dir)
            pages = 1
            for page_num in range(1, 100):
                if os.path.exists(os.path.join(output_dir, f"page_{page_num}.md")):
                    pages = page_num
            return ParseResult(status="success", text=text, pages=pages)
        except Exception as magic_err:
            # magic-pdf 失败 → 降级到 PyMuPDF
            try:
                text, pages = _parse_with_pymupdf(content)
                if text.strip():
                    return ParseResult(
                        status="success" if len(text) >= 50 else "low_text",
                        text=text,
                        pages=pages,
                    )
            except Exception as fitz_err:
                return ParseResult(
                    status="failed",
                    text="",
                    pages=0,
                    error=f"magic-pdf: {magic_err}; pymupdf: {fitz_err}",
                )
            return ParseResult(
                status="failed",
                text="",
                pages=0,
                error=str(magic_err),
            )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/")
async def root():
    return {"message": "MinerU PDF Parser API", "backends": ["magic-pdf CLI", "PyMuPDF fallback"]}
