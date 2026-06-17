"""文件 / 字节级加密辅助。

复用 ``app.models.types._get_fernet``（项目级 Fernet 实例获取），不另起炉灶。

API:
- ``encrypt_bytes(plaintext)``：Fernet 加密字节；Fernet key 未配置时透传（开发态）
- ``decrypt_bytes(ciphertext)``：Fernet 解密字节；非法密文抛 ``CryptoError``
- ``generate_data_key()``：返回 ``(plaintext_key, wrapped_key)`` 32 字节 AES-256 数据密钥 + Fernet 包装
  （供未来 envelope encryption 场景使用，本任务暂不调用）

策略说明（见 design.md 第 22 行）：
- PII 字段：``EncryptedString``（已实现）走 Fernet 列加密
- 简历文件：``StorageAdapter.put`` 默认走 SSE-S3（AES-256），客户端不持有文件密钥
- 本模块提供的 ``encrypt_bytes`` 仅供未来客户端加密场景（如某些云不支持 SSE-S3）
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken

from app.models.types import _get_fernet


class CryptoError(Exception):
    """加解密通用错误（密钥未配置但请求强加密 / 密文损坏）。"""


def encrypt_bytes(plaintext: bytes) -> bytes:
    """用 Fernet 加密字节；返回 Fernet token 字节。

    若 ``settings.FERNET_KEY`` 为空（开发期），透传明文以保持与 ``EncryptedString``
    一致的降级行为。生产环境必须配置非空 key（启动校验见 settings.py）。

    Raises:
        CryptoError: 内部异常包装（避免泄漏实现细节）
    """
    f = _get_fernet()
    if f is None:
        return plaintext
    try:
        return f.encrypt(plaintext)
    except Exception as exc:  # Fernet.encrypt 一般不抛，但兜底
        raise CryptoError("加密失败") from exc


def decrypt_bytes(ciphertext: bytes) -> bytes:
    """Fernet 解密字节。

    Fernet key 未配置时透传（兼容开发态加密但未配置 key 的历史数据）。
    密文损坏抛 ``CryptoError``。

    Raises:
        CryptoError: 密文无效或 key 不匹配
    """
    f = _get_fernet()
    if f is None:
        return ciphertext
    try:
        return f.decrypt(ciphertext)
    except InvalidToken as exc:
        raise CryptoError("密文无效或 key 不匹配") from exc


def generate_data_key() -> tuple[bytes, bytes]:
    """生成 32 字节 AES-256 数据密钥（envelope encryption 场景）。

    返回 ``(plaintext_key, wrapped_key)``：
    - ``plaintext_key``：32 字节随机密钥，仅在内存中使用，用完即清
    - ``wrapped_key``：用 Fernet 加密后的 ``plaintext_key``，可安全持久化

    若 Fernet key 未配置（开发态），``wrapped_key`` 等于 ``plaintext_key``（明文）。

    典型 envelope 用法：
        plaintext_key, wrapped_key = generate_data_key()
        # 用 plaintext_key 加密文件 → 上传密文 + wrapped_key
        # 下载后用 wrapped_key → decrypt_bytes → 还原 plaintext_key → 解密文件
    """
    plaintext = os.urandom(32)
    f = _get_fernet()
    if f is None:
        return plaintext, plaintext
    wrapped = f.encrypt(plaintext)
    return plaintext, wrapped


__all__ = ["CryptoError", "encrypt_bytes", "decrypt_bytes", "generate_data_key"]
