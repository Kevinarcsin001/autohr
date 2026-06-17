"""EncryptedString 类型 + PII 字段加解密往返测试。

测试要点：
- 写入时密文与明文不同（Fernet 加密）
- 读取时还原为明文
- 非 PII 列（如 name=非加密字段）不受影响
- Fernet key 缺失时退化为透传

策略：所有 setup/teardown 内联到测试函数内，避免 async fixture 跨 loop
（pytest-asyncio 1.x 默认每测试新建 loop，与全局 async engine 的连接池冲突）。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import AsyncSessionLocal, Base
from app.models.types import EncryptedString


class _EncryptedProbe(Base):
    """临时探测表：用 EncryptedString 单字段验证加解密往返。"""

    __tablename__ = "_test_encrypted_probe"
    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    value: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    label: Mapped[str | None] = mapped_column(String, nullable=True)


class TestEncryptedString:
    """EncryptedString 列加解密往返。"""

    @pytest.mark.asyncio
    async def test_encrypt_then_decrypt_roundtrip(self) -> None:
        """写入明文 → DB 中存密文 → ORM 读出明文。"""
        from app.core.db import engine

        # 建表（如果不存在）
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda c: _EncryptedProbe.__table__.create(c, checkfirst=True)
            )

        probe_id: uuid.UUID
        async with AsyncSessionLocal() as session:
            probe = _EncryptedProbe(value="13812345678", label="phone-test")
            session.add(probe)
            await session.commit()
            await session.refresh(probe)
            probe_id = probe.id

        # 通过 raw SQL 拿到 DB 中的原始值，应是密文
        async with AsyncSessionLocal() as session:
            raw = await session.execute(
                text("SELECT value FROM _test_encrypted_probe WHERE id = :id"),
                {"id": str(probe_id)},
            )
            raw_value = raw.scalar_one()
            assert raw_value != "13812345678"
            assert raw_value.startswith("gAAAA")  # Fernet ciphertext prefix

        # 通过 ORM 读取，应是明文
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select

            result = await session.execute(
                select(_EncryptedProbe).where(_EncryptedProbe.id == probe_id)
            )
            probe_loaded = result.scalar_one()
            assert probe_loaded.value == "13812345678"
            assert probe_loaded.label == "phone-test"


class TestEncryptedStringFallback:
    """Fernet key 缺失/损坏时的容错行为。"""

    def test_no_fernet_key_passthrough(self) -> None:
        """settings.FERNET_KEY 为空时退化为透传（开发态）。"""
        from app.core.config import settings
        from app.models.types import _get_fernet

        original = settings.FERNET_KEY
        try:
            settings.FERNET_KEY = ""
            assert _get_fernet() is None
        finally:
            settings.FERNET_KEY = original or ""

    def test_invalid_ciphertext_returns_raw(self) -> None:
        """密文损坏时原样返回，避免阻塞整个查询。"""
        decorator = EncryptedString()
        result = decorator.process_result_value("not-a-valid-fernet-token", None)
        assert result == "not-a-valid-fernet-token"
