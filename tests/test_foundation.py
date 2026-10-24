import importlib.util
import uuid
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from rfp.api.app import create_app
from rfp.core.config import Settings
from rfp.db.models import Base, Bid, Document, ProcessingJob, Status
from rfp.workers.celery_app import create_celery


def test_settings(settings):
    data = settings.model_dump()
    data["r2_account_id"] = "a" * 32
    derived = Settings(_env_file=None, **data)
    assert derived.r2_endpoint_url == f"https://{'a' * 32}.r2.cloudflarestorage.com"
    assert not derived.r2_configured
    assert "test:test" not in repr(settings)


def test_environment_loading(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://test:test@localhost/test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setenv("QDRANT_URL", "http://localhost:6333")
    monkeypatch.setenv("QDRANT_COLLECTION", "custom_collection")
    assert Settings(_env_file=None).qdrant_collection == "custom_collection"


def test_structured_logging():
    import json
    import logging

    from rfp.core.logging import JsonFormatter, request_id

    token = request_id.set("test-correlation")
    try:
        record = logging.LogRecord("rfp", logging.ERROR, "", 0, "request_failed", (), None)
        result = json.loads(JsonFormatter().format(record))
        assert result["request_id"] == "test-correlation"
        assert result["event"] == "request_failed"
        assert result["level"] == "ERROR"
    finally:
        request_id.reset(token)


@pytest.mark.parametrize(
    "field,value",
    [
        ("database_url", "sqlite:///test"),
        ("redis_url", "https://redis"),
        ("qdrant_url", "https://user:secret@example.com"),
        ("r2_endpoint_url", "https://example.com"),
        ("r2_account_id", "bad"),
        ("dependency_timeout_seconds", 0),
    ],
)
def test_invalid_settings(settings, field, value):
    data = settings.model_dump()
    data[field] = value
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **data)


def test_health_and_errors(settings, caplog):
    def broken():
        raise RuntimeError("secret-connection-string")

    app = create_app(settings, {"postgres": broken, "redis": lambda: None})

    @app.get("/explode")
    def explode():
        raise RuntimeError("private-signed-url")

    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "alive"}
        response = client.get("/health/ready", headers={"X-Request-ID": "test-123"})
        assert response.status_code == 503
        assert response.headers["X-Request-ID"] == "test-123"
        assert response.json()["dependencies"]["postgres"] == "unhealthy"
        assert response.json()["optional"]["r2"] == "not_configured"
        assert "secret" not in response.text
        missing = client.get("/missing")
        assert missing.status_code == 404
        assert missing.json()["error"]["request_id"] == missing.headers["X-Request-ID"]
        failed = client.get("/explode")
        assert failed.status_code == 500
        assert "private" not in failed.text
    assert "secret-connection-string" not in caplog.text


def test_ready_and_invalid_request_id(settings):
    with TestClient(create_app(settings, {"postgres": lambda: None})) as client:
        response = client.get("/health/ready", headers={"X-Request-ID": "x" * 100})
        assert response.status_code == 200
        assert len(response.headers["X-Request-ID"]) == 32


def test_task(settings):
    app = create_celery(settings)
    assert app.tasks["rfp.ping"].apply().get() == "pong"
    assert app.conf.task_ignore_result


@pytest.fixture
def engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_records(engine):
    with Session(engine) as session:
        bid = Bid(title="Example")
        session.add(bid)
        session.flush()
        assert isinstance(bid.id, uuid.UUID)
        assert bid.created_at and bid.updated_at and bid.status == Status.pending
        session.add(
            Document(
                bid_id=bid.id,
                filename="a.pdf",
                object_key="bids/a.pdf",
                content_type="application/pdf",
            )
        )
        session.add(ProcessingJob(bid_id=bid.id, kind="diagnostic"))
        session.commit()
        session.add(
            Document(
                bid_id=bid.id,
                filename="b.pdf",
                object_key="bids/a.pdf",
                content_type="application/pdf",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_foreign_key_and_status(engine):
    with Session(engine) as session:
        session.add(ProcessingJob(bid_id=uuid.uuid4(), kind="test"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        session.add(Bid(title="Invalid", status="invalid"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_migration_matches_models():
    path = Path("src/rfp/db/migrations/versions/0001_foundation.py")
    spec = importlib.util.spec_from_file_location("initial", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    second_spec = importlib.util.spec_from_file_location(
        "ingestion_migration", Path("src/rfp/db/migrations/versions/0002_ingestion.py")
    )
    second = importlib.util.module_from_spec(second_spec)
    second_spec.loader.exec_module(second)
    third_spec = importlib.util.spec_from_file_location(
        "chunk_migration", Path("src/rfp/db/migrations/versions/0003_chunks.py")
    )
    third = importlib.util.module_from_spec(third_spec)
    third_spec.loader.exec_module(third)
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            module.upgrade()
            second.upgrade()
            third.upgrade()
            assert compare_metadata(context, Base.metadata) == []
            third.downgrade()
            second.downgrade()
            module.downgrade()
            assert not connection.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            ).all()
    engine.dispose()
