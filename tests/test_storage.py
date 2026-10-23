from io import BytesIO
from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import EndpointConnectionError
from botocore.response import StreamingBody
from botocore.stub import Stubber

from rfp.storage.r2 import CloudflareR2Storage, ObjectNotFound, StorageError, validate_key


@pytest.fixture
def storage(settings):
    configured = settings.model_copy(
        update={
            "r2_endpoint_url": "https://example.r2.cloudflarestorage.com",
            "r2_bucket_name": "test",
        }
    )
    from pydantic import SecretStr

    configured.r2_access_key_id = SecretStr("testing")
    configured.r2_secret_access_key = SecretStr("testing")
    client = boto3.client(
        "s3",
        endpoint_url=configured.r2_endpoint_url,
        aws_access_key_id="testing",
        aws_secret_access_key="testing",
        region_name="auto",
    )
    return CloudflareR2Storage(configured, client)


def test_storage_roundtrip_contract(storage):
    body = BytesIO(b"document")
    raw = BytesIO(b"document")
    stream = StreamingBody(raw, 8)
    with Stubber(storage.client) as stub:
        stub.add_response(
            "put_object",
            {},
            {
                "Bucket": "test",
                "Key": "bid/file.pdf",
                "Body": body,
                "ContentType": "application/pdf",
                "Metadata": {"source": "test"},
            },
        )
        storage.upload(
            "bid/file.pdf", body, content_type="application/pdf", metadata={"source": "test"}
        )
        stub.add_response("get_object", {"Body": stream}, {"Bucket": "test", "Key": "bid/file.pdf"})
        target = BytesIO()
        storage.download("bid/file.pdf", target)
        assert target.getvalue() == b"document"
        assert raw.closed
        stub.add_response(
            "head_object",
            {"ContentLength": 8, "Metadata": {"source": "test"}},
            {"Bucket": "test", "Key": "bid/file.pdf"},
        )
        assert storage.head("bid/file.pdf")["size"] == 8
        stub.add_response("delete_object", {}, {"Bucket": "test", "Key": "bid/file.pdf"})
        storage.delete("bid/file.pdf")
        stub.add_response("head_bucket", {}, {"Bucket": "test"})
        storage.check_bucket()
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("key", ["", "/a", "a/../b", "a//b", "a/", "a\\b", "a\n", "é" * 600])
def test_invalid_keys(key):
    with pytest.raises(ValueError):
        validate_key(key)


def test_errors_and_signing(storage):
    with Stubber(storage.client) as stub:
        stub.add_client_error("head_object", "NoSuchKey", "sensitive", 404)
        with pytest.raises(ObjectNotFound, match="^Object not found$"):
            storage.head("missing")
        stub.add_client_error("delete_object", "AccessDenied", "sensitive", 403)
        with pytest.raises(StorageError, match="^Object store operation failed$"):
            storage.delete("key")
    storage.client = Mock()
    storage.client.generate_presigned_url.return_value = "private-url"
    assert storage.presign("key", 60) == "private-url"
    storage.client.generate_presigned_url.assert_called_once_with(
        "get_object", Params={"Bucket": "test", "Key": "key"}, ExpiresIn=60
    )
    with pytest.raises(ValueError):
        storage.presign("key", 3601)
    storage.client.head_bucket.side_effect = EndpointConnectionError(endpoint_url="secret")
    with pytest.raises(StorageError, match="^Object store unavailable$"):
        storage.check_bucket()


def test_requires_config(settings):
    with pytest.raises(ValueError, match="Complete R2"):
        CloudflareR2Storage(settings)


def test_download_closes_on_target_failure(storage):
    raw = BytesIO(b"document")
    storage.client = Mock()
    storage.client.get_object.return_value = {"Body": StreamingBody(raw, 8)}
    target = Mock()
    target.write.side_effect = OSError("destination full")
    with pytest.raises(OSError, match="destination full"):
        storage.download("key", target)
    assert raw.closed
