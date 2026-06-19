"""OCR 适配器（任务 13）。

设计：
- ``OCRAdapter`` 抽象基类
- ``PaddleOCRAdapter``：进程内调 PaddleOCR（懒加载；首次调用初始化模型）
- ``StubOCRAdapter``：无 PaddleOCR 时的兜底，返回空串 + 警告（让流程继续）
- ``get_ocr_adapter()``：工厂，按 settings.OCR_BACKEND 选择
  - ``"paddle"``：尝试 import paddleocr，失败则降级 stub
  - ``"stub"``（默认）：直接返回 StubOCRAdapter

约束：
- 模型懒加载（不在 import 时初始化）—— ``__init__`` 不调 paddle
- 进程内调用（不依赖外部服务）
- 接受 ``image_bytes`` 输入，返回识别文本（已去重拼接）
"""
from __future__ import annotations

import io
import uuid
from abc import ABC, abstractmethod
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# 抽象基类
# ============================================================================


class OCRAdapter(ABC):
    """OCR 适配器接口。"""

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """实现标识（paddle / stub / ...）。"""

    @abstractmethod
    async def extract(self, image_bytes: bytes, *, langs: tuple[str, ...]) -> str:
        """对单张图片做 OCR，返回拼接后的纯文本。

        Args:
            image_bytes: 图片字节流（PNG/JPEG）
            langs: 期望语言，如 ``("ch", "en")``；adapter 自行映射到模型参数

        Returns:
            识别出的文本（多行用 ``\\n`` 连接）；无文本时返回空串
        """


# ============================================================================
# PaddleOCR Adapter（懒加载）
# ============================================================================


class PaddleOCRAdapter(OCRAdapter):
    """进程内调 PaddleOCR。

    - 首次 ``extract`` 才 ``import paddleocr`` 并初始化模型（避免 import 时长）
    - 单实例复用（PaddleOCR 模型加载昂贵）
    - 线程安全：PaddleOCR 内部用 numpy，多 worker 各自持实例即可
    """

    def __init__(self, *, lang: str = "ch", use_gpu: bool = False) -> None:
        self._lang = lang
        self._use_gpu = use_gpu
        self._engine: Any | None = None  # PaddleOCR 实例
        self._model_version: str | None = None

    @property
    def backend_name(self) -> str:
        return "paddleocr"

    def _ensure_engine(self) -> None:
        if self._engine is not None:
            return
        # 局部 import 避免模块加载时即依赖 paddle
        from paddleocr import PaddleOCR  # type: ignore[import-not-found]

        logger.info("paddleocr_initializing", lang=self._lang, use_gpu=self._use_gpu)
        self._engine = PaddleOCR(
            use_angle_cls=True,
            lang=self._lang,
            use_gpu=self._use_gpu,
            show_log=False,
        )
        try:
            self._model_version = (
                f"paddleocr-{PaddleOCR.__module__}"  # 粗略记录版本
            )
        except Exception:  # noqa: BLE001
            self._model_version = "paddleocr-unknown"
        logger.info(
            "paddleocr_ready",
            version=self._model_version,
            lang=self._lang,
        )

    async def extract(self, image_bytes: bytes, *, langs: tuple[str, ...]) -> str:
        import asyncio

        self._ensure_engine()
        assert self._engine is not None

        # PaddleOCR 是同步阻塞 → to_thread
        return await asyncio.to_thread(self._extract_sync, image_bytes)

    def _extract_sync(self, image_bytes: bytes) -> str:
        """同步调 PaddleOCR，返回拼接文本。"""
        assert self._engine is not None
        # PaddleOCR 接受文件路径或 numpy array；用 PIL 转 ndarray
        try:
            import numpy as np  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError(
                "Pillow/numpy 未安装，无法将字节流转为 PaddleOCR 输入"
            ) from exc

        with Image.open(io.BytesIO(image_bytes)) as img:
            arr = np.array(img.convert("RGB"))

        result = self._engine.ocr(arr, cls=True)
        if not result:
            return ""

        # result 结构（PaddleOCR 2.x）：list of list of [bbox, (text, score)]
        lines: list[str] = []
        for page in result:
            if not page:
                continue
            for entry in page:
                try:
                    text = entry[1][0]
                except (IndexError, TypeError):
                    continue
                if text:
                    lines.append(str(text).strip())
        return "\n".join(lines)


# ============================================================================
# Stub Adapter（兜底）
# ============================================================================


class StubOCRAdapter(OCRAdapter):
    """无 PaddleOCR 环境的兜底 adapter。

    返回空字符串并记 warning；上游根据返回值决定是否标 low_text / failed。
    """

    def __init__(self, *, reason: str = "paddleocr not installed") -> None:
        self._reason = reason
        logger.warning("ocr_stub_active", reason=reason)

    @property
    def backend_name(self) -> str:
        return "stub"

    async def extract(self, image_bytes: bytes, *, langs: tuple[str, ...]) -> str:
        logger.warning(
            "ocr_stub_extract_returned_empty",
            reason=self._reason,
            image_size=len(image_bytes),
            request_id=str(uuid.uuid4()),
        )
        return ""


# ============================================================================
# 工厂
# ============================================================================


_adapter_singleton: OCRAdapter | None = None


def get_ocr_adapter() -> OCRAdapter:
    """按 settings.OCR_BACKEND 返回 OCR adapter 单例。

    - ``"paddle"``：尝试 import paddleocr → 成功返回 PaddleOCRAdapter；失败降级 stub
    - ``"stub"``（默认）：直接返回 StubOCRAdapter
    """
    global _adapter_singleton
    if _adapter_singleton is not None:
        return _adapter_singleton

    from app.core.config import settings

    backend = (settings.OCR_BACKEND or "stub").lower()
    if backend == "paddle":
        try:
            import paddleocr  # type: ignore[import-not-found]  # noqa: F401

            _adapter_singleton = PaddleOCRAdapter(
                lang=settings.PADDLE_OCR_LANG,
            )
            return _adapter_singleton
        except ImportError as exc:
            logger.warning(
                "paddleocr_import_failed_falling_back_to_stub",
                error=str(exc),
            )
            _adapter_singleton = StubOCRAdapter(
                reason=f"paddleocr import failed: {exc}"
            )
            return _adapter_singleton

    _adapter_singleton = StubOCRAdapter(reason="OCR_BACKEND=stub")
    return _adapter_singleton


def reset_ocr_adapter() -> None:
    """测试用：重置单例（避免测试间污染）。"""
    global _adapter_singleton
    _adapter_singleton = None


__all__ = [
    "OCRAdapter",
    "PaddleOCRAdapter",
    "StubOCRAdapter",
    "get_ocr_adapter",
    "reset_ocr_adapter",
]
