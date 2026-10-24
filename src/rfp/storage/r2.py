from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, BinaryIO, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from rfp.core.config import Settings


class StorageError(Exception):
    """Sanitized object-store failure; provider detail intentionally excluded."""


class ObjectNotFound(StorageError):
    pass


class TransientStorageError(StorageError):
    """Retryable provider/network failure, sanitized like other storage errors."""


class Storage(Protocol):
    def upload(
        self,
        key: str,
        body: BinaryIO,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None: ...
    def download(self, key: str, target: BinaryIO) -> None: ...
    def head(self, key: str) -> dict[str, Any]: ...
    def delete(self, key: str) -> None: ...
    def presign(self, key: str, expires: int = 300) -> str: ...


def validate_key(key: str) -> str:
    if (
        not key
        or len(key.encode("utf-8")) > 1024
        or "\\" in key
        or any(ord(c) < 32 or ord(c) == 127 for c in key)
        or any(part in {"", ".", ".."} for part in key.split("/"))
    ):
        raise ValueError("Object key must be a relative path without empty or traversal segments")
    return key


@contextmanager
def provider_errors():
    try:
        yield
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in {"404", "NoSuchKey", "NotFound"}:
            raise ObjectNotFound("Object not found") from None
        if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) >= 500 or code in {
            "SlowDown",
            "RequestTimeout",
            "Throttling",
            "429",
        }:
            raise TransientStorageError("Object store operation failed") from None
        raise StorageError("Object store operation failed") from None
    except BotoCoreError:
        raise TransientStorageError("Object store unavailable") from None


class CloudflareR2Storage:
    def __init__(self, settings: Settings, client: Any = None):
        if not settings.r2_configured:
            raise ValueError("Complete R2 configuration is required for storage operations")
        self.bucket = settings.r2_bucket_name
        self.client = (
            client
            if client is not None
            else boto3.client(
                "s3",
                endpoint_url=settings.r2_endpoint_url,
                aws_access_key_id=settings.r2_access_key_id.get_secret_value(),
                aws_secret_access_key=settings.r2_secret_access_key.get_secret_value(),
                region_name=settings.r2_region,
                config=Config(
                    signature_version="s3v4",
                    connect_timeout=3,
                    read_timeout=10,
                    retries={"mode": "standard", "total_max_attempts": 3},
                    s3={"addressing_style": "path"},
                ),
            )
        )

    def upload(
        self,
        key: str,
        body: BinaryIO,
        *,
        content_type: str,
        metadata: Mapping[str, str] | None = None,
    ) -> None:
        key = validate_key(key)
        with provider_errors():
            self.client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                Metadata=dict(metadata or {}),
            )

    def download(self, key: str, target: BinaryIO) -> None:
        key = validate_key(key)
        with provider_errors():
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            stream = response["Body"]
            try:
                for chunk in stream.iter_chunks(chunk_size=1024 * 1024):
                    target.write(chunk)
            finally:
                stream.close()

    def head(self, key: str) -> dict[str, Any]:
        key = validate_key(key)
        with provider_errors():
            result = self.client.head_object(Bucket=self.bucket, Key=key)
            return {
                "size": result["ContentLength"],
                "content_type": result.get("ContentType"),
                "metadata": result.get("Metadata", {}),
                "etag": result.get("ETag"),
            }

    def delete(self, key: str) -> None:
        key = validate_key(key)
        with provider_errors():
            self.client.delete_object(Bucket=self.bucket, Key=key)

    def presign(self, key: str, expires: int = 300) -> str:
        key = validate_key(key)
        if not 1 <= expires <= 3600:
            raise ValueError("Presigned URL expiry must be between 1 and 3600 seconds")
        with provider_errors():
            return self.client.generate_presigned_url(
                "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires
            )

    def check_bucket(self) -> None:
        with provider_errors():
            self.client.head_bucket(Bucket=self.bucket)
