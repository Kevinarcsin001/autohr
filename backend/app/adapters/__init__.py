"""外部适配器层（LLM / 对象存储 / 加密等）。

子模块：
- ``adapters.llm``：LLM Router + Zhipu/Qwen/Mock adapters
- ``adapters.storage``：S3 兼容对象存储（MinIO/S3/OSS）
- ``adapters.crypto``：Fernet 字节级加密辅助
"""
from __future__ import annotations
