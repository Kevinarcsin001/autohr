"""S3 兼容对象存储适配器（开发用 MinIO，生产用 S3 / 阿里云 OSS）。

接口：
- ``put(key, data, mime, encrypt=True)``：默认走 SSE-S3（AES-256 服务端加密）
- ``get(key)``：返回对象字节
- ``delete(key)``：删除对象（幂等，不存在不抛错）
- ``exists(key)``：检查对象是否存在
- ``signed_url(key, expires, method)``：生成短期签名 URL（默认 5 分钟）

错误层级：
- ``StorageError``（基类）
- ``StorageNotFoundError``：404 / 对象不存在
- ``StorageAuthError``：403 / 鉴权失败 / 凭据错误
- ``StorageTimeoutError``：连接 / 读取超时

异步实现：boto3 client 是同步阻塞，所有 IO 通过 ``asyncio.to_thread`` 包装。

Key 命名约定（由调用方 service 层负责）：``{team_id}/{resume_id}/{uuid}.{ext}``
adapter 本身不感知 team 语义，跨 team 访问 404 由 service 层校验前缀。
"""
from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError, EndpointConnectionError

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================================
# 错误层级
# ============================================================================


class StorageError(Exception):
    """存储适配器通用错误基类。"""


class StorageNotFoundError(StorageError):
    """对象不存在（404 / NoSuchKey）。"""


class StorageAuthError(StorageError):
    """鉴权失败（403 / InvalidAccessKeyId / SignatureDoesNotMatch）。"""


class StorageTimeoutError(StorageError):
    """连接 / 读取超时。"""


# ============================================================================
# boto3 ClientError → StorageError 路由
# ============================================================================

# 4xx 错误码 → 异常类型映射
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "404", "NoSuchBucket", "NotFound"})
_AUTH_CODES = frozenset(
    {
        "AccessDenied",
        "Forbidden",
        "InvalidAccessKeyId",
        "SignatureDoesNotMatch",
        "Unauthorized",
    }
)


def _map_boto_error(exc: Exception) -> StorageError:
    """把 botocore 异常映射为 StorageError 子类（返回新异常，caller 负责 raise）。"""
    if isinstance(exc, EndpointConnectionError):
        return StorageTimeoutError(f"无法连接对象存储: {exc}")
    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        http_status = exc.response.get("ResponseMetadata", {}).get(
            "HTTPStatusCode", 0
        )
        if code in _NOT_FOUND_CODES or http_status == 404:
            return StorageNotFoundError(str(exc))
        if code in _AUTH_CODES or http_status == 403:
            return StorageAuthError(str(exc))
        if "timeout" in str(exc).lower():
            return StorageTimeoutError(str(exc))
        return StorageError(str(exc))
    if isinstance(exc, TimeoutError):
        return StorageTimeoutError(str(exc))
    return StorageError(str(exc))


# ============================================================================
# Protocol
# ============================================================================


@runtime_checkable
class BaseStorageAdapter(Protocol):
    """存储适配器接口。

    所有方法为 async；实现方负责把同步 boto3 调用包装为异步（``asyncio.to_thread``）。
    """

    name: str

    async def put(
        self, key: str, data: bytes, *, mime: str, encrypt: bool = True
    ) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None: ...

    async def exists(self, key: str) -> bool: ...

    async def signed_url(
        self,
        key: str,
        *,
        expires: int | None = None,
        method: str = "GET",
    ) -> str: ...


# ============================================================================
# S3StorageAdapter
# ============================================================================


class S3StorageAdapter:
    """S3 兼容存储适配器（MinIO / AWS S3 / 阿里云 OSS）。

    凭据来自 ``settings.MINIO_*``（命名兼容历史）。生产环境只需 override
    环境变量即可切换到 AWS S3 / OSS。

    加密：``put_object`` 默认带 header ``x-amz-server-side-encryption: AES256``
    （SSE-S3），由服务端用 AES-256 加密；客户端不持有文件密钥。
    """

    name = "s3"

    def __init__(
        self,
        *,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        bucket: str | None = None,
        secure: bool | None = None,
    ) -> None:
        self.access_key = access_key or settings.MINIO_ACCESS_KEY
        self.secret_key = secret_key or settings.MINIO_SECRET_KEY
        self.bucket = bucket or settings.MINIO_BUCKET
        self.secure = settings.MINIO_SECURE if secure is None else secure

        if endpoint_url:
            self.endpoint_url = endpoint_url
        else:
            # settings.MINIO_ENDPOINT 形如 "minio:9000"，无 scheme
            scheme = "https" if self.secure else "http"
            self.endpoint_url = f"{scheme}://{settings.MINIO_ENDPOINT}"

        config = BotoConfig(
            connect_timeout=settings.STORAGE_CONNECT_TIMEOUT_SECONDS,
            read_timeout=settings.STORAGE_READ_TIMEOUT_SECONDS,
            retries={"max_attempts": 3, "mode": "standard"},
            signature_version="s3v4",
        )
        self._client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            config=config,
        )

    # ----- 同步实现（在 to_thread 中调用） -----

    def _put_sync(
        self, key: str, data: bytes, mime: str, encrypt: bool
    ) -> None:
        params: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": data,
            "ContentType": mime,
        }
        if encrypt:
            # SSE-S3：服务端用 AES-256 加密；MinIO/S3/OSS 均支持
            params["ServerSideEncryption"] = "AES256"
        self._client.put_object(**params)

    def _get_sync(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self.bucket, Key=key)
        body = resp["Body"].read()
        return body

    def _get_range_sync(self, key: str, start: int, end: int) -> bytes:
        """读取对象的字节区间 [start, end]（inclusive，遵循 HTTP Range 语义）。

        用于 MIME 嗅探：只取前 N 字节而非整文件，避免 20 MB 简历全量读入。
        """
        # S3 Range header 是 inclusive [start, end]；end 应 ≥ start
        if start < 0 or end < start:
            raise ValueError(f"非法 range: start={start}, end={end}")
        resp = self._client.get_object(
            Bucket=self.bucket,
            Key=key,
            Range=f"bytes={start}-{end}",
        )
        return resp["Body"].read()

    def _delete_sync(self, key: str) -> None:
        # 幂等：不存在不抛错（delete_object 永远返回 204）
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def _exists_sync(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise

    def _signed_url_sync(
        self, key: str, expires: int, method: str
    ) -> str:
        # HTTP method → boto3 operation name
        method_map = {
            "GET": "get_object",
            "PUT": "put_object",
            "HEAD": "head_object",
            "DELETE": "delete_object",
        }
        op = method_map.get(method.upper(), "get_object")

        # 如果设置了公开端点，用它生成浏览器可访问的签名 URL
        public_endpoint = settings.MINIO_PUBLIC_ENDPOINT
        if public_endpoint:
            scheme = "https" if self.secure else "http"
            public_url = f"{scheme}://{public_endpoint}"
            public_client = boto3.client(
                "s3",
                endpoint_url=public_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                config=BotoConfig(
                    signature_version="s3v4",
                ),
            )
            return public_client.generate_presigned_url(
                op,
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires,
            )

        return self._client.generate_presigned_url(
            op,
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires,
        )

    # ----- 异步包装 -----

    async def put(
        self,
        key: str,
        data: bytes,
        *,
        mime: str,
        encrypt: bool = True,
    ) -> None:
        try:
            await asyncio.to_thread(self._put_sync, key, data, mime, encrypt)
        except (ClientError, EndpointConnectionError, TimeoutError) as exc:
            raise _map_boto_error(exc) from exc

    async def get(self, key: str) -> bytes:
        try:
            return await asyncio.to_thread(self._get_sync, key)
        except (ClientError, EndpointConnectionError, TimeoutError) as exc:
            raise _map_boto_error(exc) from exc

    async def get_range(self, key: str, start: int, end: int) -> bytes:
        """异步读取对象字节区间（MIME 嗅探专用，避免全量下载）。"""
        try:
            return await asyncio.to_thread(self._get_range_sync, key, start, end)
        except (ClientError, EndpointConnectionError, TimeoutError) as exc:
            raise _map_boto_error(exc) from exc

    async def delete(self, key: str) -> None:
        try:
            await asyncio.to_thread(self._delete_sync, key)
        except (ClientError, EndpointConnectionError, TimeoutError) as exc:
            raise _map_boto_error(exc) from exc

    async def exists(self, key: str) -> bool:
        try:
            return await asyncio.to_thread(self._exists_sync, key)
        except (ClientError, EndpointConnectionError, TimeoutError) as exc:
            raise _map_boto_error(exc) from exc

    async def signed_url(
        self,
        key: str,
        *,
        expires: int | None = None,
        method: str = "GET",
    ) -> str:
        # 服务端校验：1 ≤ expires ≤ 3600
        eff_expires = (
            expires if expires is not None else settings.STORAGE_SIGNED_URL_EXPIRE_SECONDS
        )
        if not 1 <= eff_expires <= 3600:
            raise ValueError(
                f"expires 必须在 1-3600 秒之间，收到 {eff_expires}"
            )
        try:
            url = await asyncio.to_thread(
                self._signed_url_sync, key, eff_expires, method
            )
        except (ClientError, EndpointConnectionError, TimeoutError) as exc:
            raise _map_boto_error(exc) from exc
        return url

    # ----- 维护工具（测试 / 脚本用，不在 Protocol） -----

    def _list_all_keys_sync(self, prefix: str = "") -> list[str]:
        """列出 bucket 中所有匹配 prefix 的 key（仅测试用）。"""
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    async def list_keys(self, prefix: str = "") -> list[str]:
        """异步列出（测试用）。"""
        return await asyncio.to_thread(self._list_all_keys_sync, prefix)


# ============================================================================
# 工厂
# ============================================================================


_adapter_instance: S3StorageAdapter | None = None


def get_storage() -> S3StorageAdapter:
    """返回应用级单例 S3StorageAdapter。

    多次调用返回同一实例（boto3 client 创建开销较高，session 复用）。
    """
    global _adapter_instance
    if _adapter_instance is None:
        _adapter_instance = S3StorageAdapter()
    return _adapter_instance


def reset_storage() -> None:
    """测试用：重置单例（不删 bucket 内对象）。"""
    global _adapter_instance
    _adapter_instance = None


__all__ = [
    "BaseStorageAdapter",
    "S3StorageAdapter",
    "StorageError",
    "StorageNotFoundError",
    "StorageAuthError",
    "StorageTimeoutError",
    "get_storage",
    "reset_storage",
]
