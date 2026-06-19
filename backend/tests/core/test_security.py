"""JWT (RS256) 签发/校验/过期 + bcrypt 哈希 单元测试。"""
from __future__ import annotations

import time
from datetime import timedelta
from uuid import uuid4

import pytest
from jose import JWTError

from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_token,
    hash_password,
    verify_password,
)

# ============================================================================
# bcrypt 密码哈希
# ============================================================================


class TestPasswordHashing:
    """密码哈希：bcrypt。"""

    def test_hash_returns_bcrypt_hash(self) -> None:
        """hash_password 返回 bcrypt 格式（$2b$）。"""
        hashed = hash_password("secret123!")
        assert hashed.startswith("$2")
        assert len(hashed) >= 50

    def test_hash_salts_uniquely(self) -> None:
        """相同明文产生不同哈希（盐随机）。"""
        h1 = hash_password("same-password")
        h2 = hash_password("same-password")
        assert h1 != h2

    def test_verify_correct_password(self) -> None:
        """正确密码通过校验。"""
        hashed = hash_password("correct horse battery staple")
        assert verify_password("correct horse battery staple", hashed) is True

    def test_verify_wrong_password(self) -> None:
        """错误密码拒绝。"""
        hashed = hash_password("right-password")
        assert verify_password("wrong-password", hashed) is False

    def test_verify_corrupted_hash_returns_false(self) -> None:
        """损坏的哈希不抛异常，返回 False。"""
        assert verify_password("any", "not-a-valid-hash") is False

    def test_verify_empty_inputs(self) -> None:
        """空字符串 / 空哈希返回 False。"""
        assert verify_password("", "$2b$12$abc") is False
        assert verify_password("any", "") is False

    def test_hash_empty_password_raises(self) -> None:
        """hash 空密码抛 ValueError。"""
        with pytest.raises(ValueError):
            hash_password("")


# ============================================================================
# 生成 token
# ============================================================================


class TestGenerateToken:
    def test_generate_token_is_urlsafe(self) -> None:
        token = generate_token(16)
        assert isinstance(token, str)
        assert len(token) > 16

    def test_generate_token_is_unique(self) -> None:
        t1 = generate_token()
        t2 = generate_token()
        assert t1 != t2


# ============================================================================
# JWT 签发 / 校验
# ============================================================================


class TestJWT:
    """JWT RS256 签发与校验。"""

    def test_access_token_roundtrip(self, rsa_keys: dict[str, str]) -> None:
        """access token 签发 → 解码 → claims 一致。"""
        user_id = uuid4()
        token = create_access_token(user_id, extra_claims={"role": "admin"})

        payload = decode_token(token, expected_type="access")

        assert payload["sub"] == str(user_id)
        assert payload["type"] == "access"
        assert payload["role"] == "admin"
        assert "exp" in payload
        assert "iat" in payload
        assert "jti" in payload

    def test_refresh_token_roundtrip(self, rsa_keys: dict[str, str]) -> None:
        """refresh token 签发 → 解码。"""
        user_id = uuid4()
        token = create_refresh_token(user_id)

        payload = decode_token(token, expected_type="refresh")

        assert payload["sub"] == str(user_id)
        assert payload["type"] == "refresh"

    def test_token_type_mismatch_rejected(
        self, rsa_keys: dict[str, str]
    ) -> None:
        """access token 用 refresh 期望校验 → 拒绝。"""
        token = create_access_token(uuid4())
        with pytest.raises(JWTError):
            decode_token(token, expected_type="refresh")

    def test_expired_token_rejected(self, rsa_keys: dict[str, str]) -> None:
        """过期 token 拒绝。"""
        token = create_access_token(uuid4(), expires_delta=timedelta(seconds=-1))
        with pytest.raises(JWTError):
            decode_token(token)

    def test_token_signed_with_wrong_key_rejected(
        self, rsa_keys: dict[str, str], tmp_path
    ) -> None:
        """用另一对密钥签发的 token 用本服务公钥校验 → 拒绝。"""
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from jose import jwt as jose_jwt

        from app.core.config import settings

        # 用 cryptography 生成另一对密钥
        other_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        other_priv_path = tmp_path / "other_private.pem"
        other_priv_path.write_bytes(
            other_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

        # 用错误私钥签发
        wrong_token = jose_jwt.encode(
            {"sub": "x", "type": "access", "exp": int(time.time()) + 3600},
            other_priv_path.read_text(),
            algorithm=settings.JWT_ALGORITHM,
        )

        with pytest.raises(JWTError):
            decode_token(wrong_token)

    def test_extra_claims_propagate(self, rsa_keys: dict[str, str]) -> None:
        """extra_claims 正确写入 payload。"""
        token = create_access_token(
            "user-xyz",
            extra_claims={"team_id": "team-1", "role": "member"},
        )
        payload = decode_token(token)
        assert payload["team_id"] == "team-1"
        assert payload["role"] == "member"
