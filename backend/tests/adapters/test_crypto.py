"""crypto.py 单元测试。

覆盖：
- encrypt_bytes / decrypt_bytes 往返
- Fernet key 未配置时降级（透传）
- 非法密文 → CryptoError
- generate_data_key 返回 (plaintext 32 字节, wrapped) 元组
"""
from __future__ import annotations

import pytest

from app.adapters import crypto
from app.core.config import settings


# ============================================================================
# 工具：测试用 Fernet key 注入
# ============================================================================


@pytest.fixture
def with_fernet_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """注入一个有效 Fernet key（base64 编码的 32 字节）。"""
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setattr(settings, "FERNET_KEY", key)
    # 清缓存（_get_fernet 每次都读 settings，无需清）
    return key


@pytest.fixture
def without_fernet_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """清空 Fernet key（模拟开发态降级）。"""
    monkeypatch.setattr(settings, "FERNET_KEY", "")


# ============================================================================
# encrypt_bytes / decrypt_bytes 往返
# ============================================================================


def test_encrypt_decrypt_roundtrip(with_fernet_key: str) -> None:
    plaintext = b"hello resume file content with\xc3\xa9 unicode"
    ciphertext = crypto.encrypt_bytes(plaintext)
    assert ciphertext != plaintext
    assert crypto.decrypt_bytes(ciphertext) == plaintext


def test_encrypt_bytes_empty_input(with_fernet_key: str) -> None:
    """空字节也能加密解密。"""
    ciphertext = crypto.encrypt_bytes(b"")
    assert ciphertext != b""
    assert crypto.decrypt_bytes(ciphertext) == b""


def test_encrypt_bytes_large_payload(with_fernet_key: str) -> None:
    """大 payload（1 MB）往返。"""
    plaintext = b"x" * (1024 * 1024)
    ciphertext = crypto.encrypt_bytes(plaintext)
    assert crypto.decrypt_bytes(ciphertext) == plaintext


# ============================================================================
# 降级：Fernet key 未配置时透传
# ============================================================================


def test_encrypt_decrypt_passthrough_without_key(without_fernet_key: None) -> None:
    """Fernet key 未配置 → encrypt/decrypt 透传，与 EncryptedString 行为一致。"""
    plaintext = b"plaintext bytes"
    assert crypto.encrypt_bytes(plaintext) == plaintext
    assert crypto.decrypt_bytes(plaintext) == plaintext


# ============================================================================
# 非法密文
# ============================================================================


def test_decrypt_invalid_ciphertext_raises(with_fernet_key: str) -> None:
    with pytest.raises(crypto.CryptoError, match="密文无效"):
        crypto.decrypt_bytes(b"not-a-valid-fernet-token")


def test_decrypt_corrupted_ciphertext_raises(with_fernet_key: str) -> None:
    """密文被篡改 → CryptoError。"""
    plaintext = b"original"
    ciphertext = bytearray(crypto.encrypt_bytes(plaintext))
    # 翻转最后一个字节
    ciphertext[-1] ^= 0xFF
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_bytes(bytes(ciphertext))


# ============================================================================
# generate_data_key
# ============================================================================


def test_generate_data_key_returns_32_byte_plaintext(with_fernet_key: str) -> None:
    plaintext_key, wrapped_key = crypto.generate_data_key()
    assert len(plaintext_key) == 32
    # wrapped 是 Fernet token（base64 编码），能解回 plaintext_key
    assert crypto.decrypt_bytes(wrapped_key) == plaintext_key


def test_generate_data_key_uniqueness(with_fernet_key: str) -> None:
    """每次生成不同 key。"""
    k1 = crypto.generate_data_key()
    k2 = crypto.generate_data_key()
    assert k1[0] != k2[0]
    assert k1[1] != k2[1]


def test_generate_data_key_passthrough_without_key(without_fernet_key: None) -> None:
    """无 Fernet key 时，wrapped == plaintext（明文）。"""
    plaintext_key, wrapped_key = crypto.generate_data_key()
    assert len(plaintext_key) == 32
    assert wrapped_key == plaintext_key
