"""密码哈希（bcrypt）+ JWT (RS256) 签发/校验工具。

设计：
- 密码用 bcrypt（cost=12），直接调用 ``bcrypt`` 库
  （passlib 1.7.4 与 bcrypt 4.1+ 存在兼容性问题，会误抛 "password cannot be
  longer than 72 bytes"）。
- JWT 用 RS256：私钥签发，公钥校验。私钥仅 backend 持有，公钥可分发给内部微服务。
- 私钥/公钥从 settings.JWT_PRIVATE_KEY_PATH / JWT_PUBLIC_KEY_PATH 读取（首次读取后缓存）。
- token 包含 jti（用于后续黑名单 / 撤销列表）。
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal, overload
from uuid import UUID

import bcrypt as _bcrypt
from jose import JWTError, jwt

from app.core.config import settings


# ============================================================================
# 密码哈希（bcrypt，直接调用 bcrypt 库）
# ============================================================================

_BCRYPT_ROUNDS = 12


def _truncate_for_bcrypt(password: str) -> bytes:
    """bcrypt 限制 72 字节输入，超长截断（UTF-8 编码后）。"""
    return password.encode("utf-8")[:72]


def hash_password(plain: str) -> str:
    """对明文密码做 bcrypt 哈希。

    Args:
        plain: 明文密码（非空）

    Returns:
        bcrypt 哈希字符串（``$2b$...``）

    Raises:
        ValueError: 明文为空
    """
    if not plain:
        raise ValueError("Password must not be empty")
    salt = _bcrypt.gensalt(rounds=_BCRYPT_ROUNDS)
    return _bcrypt.hashpw(_truncate_for_bcrypt(plain), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码与 bcrypt 哈希是否匹配。

    任何异常都返回 False（避免泄露具体错误信息）。
    """
    if not plain or not hashed:
        return False
    try:
        return _bcrypt.checkpw(
            _truncate_for_bcrypt(plain), hashed.encode("utf-8")
        )
    except (ValueError, TypeError):
        return False


def generate_token(nbytes: int = 32) -> str:
    """生成 URL 安全随机 token，用于邀请链接 / 密码重置等。"""
    return secrets.token_urlsafe(nbytes)


# ============================================================================
# JWT (RS256) 密钥加载（带缓存）
# ============================================================================

_PRIVATE_KEY_CACHE: str | None = None
_PUBLIC_KEY_CACHE: str | None = None


def _read_private_key() -> str:
    """从 settings.JWT_PRIVATE_KEY_PATH 读取 PEM 私钥（带缓存）。"""
    global _PRIVATE_KEY_CACHE
    if _PRIVATE_KEY_CACHE is None:
        path = Path(settings.JWT_PRIVATE_KEY_PATH)
        if not path.exists():
            raise FileNotFoundError(
                f"JWT private key not found at {path}. "
                "Run `make gen-keys` to generate it."
            )
        _PRIVATE_KEY_CACHE = path.read_text(encoding="utf-8")
    return _PRIVATE_KEY_CACHE


def _read_public_key() -> str:
    """从 settings.JWT_PUBLIC_KEY_PATH 读取 PEM 公钥（带缓存）。"""
    global _PUBLIC_KEY_CACHE
    if _PUBLIC_KEY_CACHE is None:
        path = Path(settings.JWT_PUBLIC_KEY_PATH)
        if not path.exists():
            raise FileNotFoundError(
                f"JWT public key not found at {path}. "
                "Run `make gen-keys` to generate it."
            )
        _PUBLIC_KEY_CACHE = path.read_text(encoding="utf-8")
    return _PUBLIC_KEY_CACHE


def reset_key_cache() -> None:
    """重置密钥缓存（测试用）。"""
    global _PRIVATE_KEY_CACHE, _PUBLIC_KEY_CACHE
    _PRIVATE_KEY_CACHE = None
    _PUBLIC_KEY_CACHE = None


# ============================================================================
# JWT 签发
# ============================================================================

TokenTypes = Literal["access", "refresh"]


def create_access_token(
    subject: str | UUID,
    extra_claims: dict[str, Any] | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """签发 access token（RS256）。

    Args:
        subject: 用户 ID（sub claim）
        extra_claims: 额外 claims，如 {"team_id": "...", "role": "admin"}
        expires_delta: 自定义过期时间；不传则用 settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES

    Returns:
        JWT 字符串
    """
    return _create_token(
        subject=subject,
        token_type="access",
        expires_delta=expires_delta
        or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES),
        extra_claims=extra_claims,
    )


def create_refresh_token(
    subject: str | UUID,
    expires_delta: timedelta | None = None,
) -> str:
    """签发 refresh token（RS256）。"""
    return _create_token(
        subject=subject,
        token_type="refresh",
        expires_delta=expires_delta
        or timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
    )


def _create_token(
    subject: str | UUID,
    token_type: TokenTypes,
    expires_delta: timedelta,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "iat": now,
        "exp": now + expires_delta,
        "type": token_type,
        "jti": generate_token(16),
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _read_private_key(), algorithm=settings.JWT_ALGORITHM)


# ============================================================================
# JWT 校验
# ============================================================================

@overload
def decode_token(token: str, expected_type: TokenTypes) -> dict[str, Any]: ...
@overload
def decode_token(token: str, expected_type: None = None) -> dict[str, Any]: ...


def decode_token(
    token: str, expected_type: TokenTypes | None = None
) -> dict[str, Any]:
    """解码并校验 JWT。

    Args:
        token: JWT 字符串
        expected_type: 期望的 token 类型（'access' / 'refresh'）；None 跳过类型检查

    Returns:
        JWT payload dict

    Raises:
        JWTError: token 无效 / 过期 / 签名错误 / 类型不匹配
    """
    payload = jwt.decode(
        token,
        _read_public_key(),
        algorithms=[settings.JWT_ALGORITHM],
    )
    if expected_type and payload.get("type") != expected_type:
        raise JWTError(
            f"Invalid token type: expected {expected_type!r}, "
            f"got {payload.get('type')!r}"
        )
    return payload
