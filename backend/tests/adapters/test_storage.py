"""S3StorageAdapter 集成测试（依赖真实 MinIO 容器）。

覆盖：
- put → get 往返（含 SSE-S3 头校验）
- get 不存在 key → StorageNotFoundError
- delete 幂等
- exists true/false
- signed_url GET 可用 + 过期拒绝
- signed_url PUT 可写入
- signed_url expires 边界校验
- 错误类型映射（StorageNotFoundError / StorageAuthError）

测试隔离：每个测试通过 list_objects_v2 + delete_objects 清空 bucket。
"""
from __future__ import annotations

import asyncio
import time
from uuid import uuid4

import boto3
import httpx
import pytest
from botocore.client import Config as BotoConfig

from app.adapters.storage import (
    S3StorageAdapter,
    StorageAuthError,
    StorageNotFoundError,
    get_storage,
    reset_storage,
)
from app.core.config import settings

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch) -> S3StorageAdapter:
    """每个测试用新的 adapter 实例（避免单例污染）。"""
    reset_storage()
    a = S3StorageAdapter()
    yield a
    reset_storage()


def _purge_bucket(adapter: S3StorageAdapter) -> None:
    """同步清空 bucket（仅测试用）。"""
    paginator = adapter._client.get_paginator("list_objects_v2")
    keys: list[dict] = []
    for page in paginator.paginate(Bucket=adapter.bucket):
        for obj in page.get("Contents", []):
            keys.append({"Key": obj["Key"]})
    if keys:
        adapter._client.delete_objects(Bucket=adapter.bucket, Delete={"Objects": keys})


@pytest.fixture(autouse=True)
def clean_bucket(adapter: S3StorageAdapter):
    _purge_bucket(adapter)
    yield
    _purge_bucket(adapter)


def _rand_key() -> str:
    return f"test/{uuid4().hex}.bin"


# ============================================================================
# put / get 往返
# ============================================================================


async def test_put_and_get_roundtrip(adapter: S3StorageAdapter) -> None:
    key = _rand_key()
    payload = b"resume content \x00\x01 with binary"
    await adapter.put(key, payload, mime="application/octet-stream")
    fetched = await adapter.get(key)
    assert fetched == payload


async def test_put_with_unicode_content(adapter: S3StorageAdapter) -> None:
    key = _rand_key()
    payload = "简历内容".encode()
    await adapter.put(key, payload, mime="text/plain; charset=utf-8")
    assert (await adapter.get(key)) == payload


# ============================================================================
# SSE-S3 加密头校验
# ============================================================================


async def test_put_uses_sse_aes256_header(adapter: S3StorageAdapter) -> None:
    """SSE-S3：head_object 应返回 ServerSideEncryption=AES256。"""
    key = _rand_key()
    await adapter.put(key, b"x", mime="text/plain", encrypt=True)
    head = adapter._client.head_object(Bucket=adapter.bucket, Key=key)
    assert head.get("ServerSideEncryption") == "AES256"


async def test_put_without_sse_skips_header(adapter: S3StorageAdapter) -> None:
    """encrypt=False 不带 SSE header（用于不支持 SSE 的存储后端）。"""
    key = _rand_key()
    await adapter.put(key, b"x", mime="text/plain", encrypt=False)
    head = adapter._client.head_object(Bucket=adapter.bucket, Key=key)
    assert "ServerSideEncryption" not in head


# ============================================================================
# get / exists 不存在
# ============================================================================


async def test_get_nonexistent_raises_not_found(adapter: S3StorageAdapter) -> None:
    with pytest.raises(StorageNotFoundError):
        await adapter.get(_rand_key())


async def test_exists_returns_false_for_nonexistent(
    adapter: S3StorageAdapter,
) -> None:
    assert await adapter.exists(_rand_key()) is False


async def test_exists_returns_true_after_put(adapter: S3StorageAdapter) -> None:
    key = _rand_key()
    await adapter.put(key, b"x", mime="text/plain")
    assert await adapter.exists(key) is True


# ============================================================================
# delete 幂等
# ============================================================================


async def test_delete_is_idempotent(adapter: S3StorageAdapter) -> None:
    """delete 不存在 key 不抛错。"""
    key = _rand_key()
    await adapter.delete(key)  # 不存在
    # 再 put 后 delete，再 delete 应仍幂等
    await adapter.put(key, b"x", mime="text/plain")
    await adapter.delete(key)
    await adapter.delete(key)  # 已删
    assert await adapter.exists(key) is False


# ============================================================================
# signed_url GET 可用 + 过期拒绝
# ============================================================================


async def test_signed_url_get_works(adapter: S3StorageAdapter) -> None:
    key = _rand_key()
    payload = b"downloaded content"
    await adapter.put(key, payload, mime="text/plain")

    # 容器内通过 docker network 访问 minio host
    url = await adapter.signed_url(key, expires=60)
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    assert resp.status_code == 200
    assert resp.content == payload


async def test_signed_url_expires_rejects(adapter: S3StorageAdapter) -> None:
    """签名 URL 过期后 GET → 403。"""
    key = _rand_key()
    await adapter.put(key, b"temp", mime="text/plain")
    url = await adapter.signed_url(key, expires=1)

    # 等 2 秒让 URL 过期
    await asyncio.sleep(2)

    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
    # MinIO 对过期 URL 返回 403
    assert resp.status_code in (403, 400)


# ============================================================================
# signed_url PUT 可写
# ============================================================================


async def test_signed_url_put_writes(adapter: S3StorageAdapter) -> None:
    key = _rand_key()
    url = await adapter.signed_url(key, expires=60, method="PUT")

    async with httpx.AsyncClient() as client:
        resp = await client.put(
            url,
            content=b"client-uploaded",
            headers={"Content-Type": "application/octet-stream"},
        )
    assert resp.status_code == 200
    assert await adapter.get(key) == b"client-uploaded"


# ============================================================================
# expires 边界
# ============================================================================


async def test_signed_url_expires_zero_rejected(adapter: S3StorageAdapter) -> None:
    with pytest.raises(ValueError, match="1-3600"):
        await adapter.signed_url(_rand_key(), expires=0)


async def test_signed_url_expires_too_large_rejected(
    adapter: S3StorageAdapter,
) -> None:
    with pytest.raises(ValueError, match="1-3600"):
        await adapter.signed_url(_rand_key(), expires=3601)


async def test_signed_url_expires_uses_default_when_none(
    adapter: S3StorageAdapter,
) -> None:
    """expires=None 用 settings.STORAGE_SIGNED_URL_EXPIRE_SECONDS。"""
    url = await adapter.signed_url(_rand_key())
    # URL 应包含 X-Amz-Expires=300（默认 5 分钟）
    assert "X-Amz-Expires=300" in url or "X-Amz-Expires%3D300" in url


# ============================================================================
# 错误映射：鉴权失败
# ============================================================================


async def test_get_with_wrong_credentials_raises_auth() -> None:
    """错误凭据 → StorageAuthError。"""
    bad = S3StorageAdapter(
        access_key="wrong", secret_key="wrongsecret"
    )
    with pytest.raises(StorageAuthError):
        await bad.get(_rand_key())


# ============================================================================
# 工厂单例
# ============================================================================


async def test_get_storage_returns_singleton() -> None:
    reset_storage()
    a1 = get_storage()
    a2 = get_storage()
    assert a1 is a2
