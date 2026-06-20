"""解析器基类 — 定义通用 ParserResult。"""

from dataclasses import dataclass, field


@dataclass
class ParserResult:
    """解析器返回结果。"""

    status: str  # success | low_text | failed
    text: str
    pages: int = 1
    error: str | None = None
