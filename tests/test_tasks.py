import uuid
from unittest.mock import Mock

import pytest
from celery.exceptions import Retry

from rfp.storage.r2 import TransientStorageError
from rfp.workers.celery_app import create_celery


def test_ingestion_task_registration_retry_and_cleanup(settings, monkeypatch):
    app = create_celery(settings)
    task = app.tasks["rfp.ingest"]
    assert task.acks_late and task.max_retries == 3
    engine = Mock()
    monkeypatch.setattr("rfp.db.session.create_db_engine", lambda config: engine)
    monkeypatch.setattr("rfp.storage.r2.CloudflareR2Storage", lambda config: Mock())
    process = Mock(side_effect=TransientStorageError("private-secret"))
    monkeypatch.setattr("rfp.ingestion.service.process_job", process)
    retry = Mock(side_effect=Retry("sanitized retry"))
    monkeypatch.setattr(task, "retry", retry)
    with pytest.raises(Retry):
        task.run(str(uuid.uuid4()))
    engine.dispose.assert_called_once()
    assert "private-secret" not in str(retry.call_args)


def test_checkpoint_task_continuation(settings, monkeypatch):
    app = create_celery(settings)
    task = app.tasks["rfp.ingest"]
    engine = Mock()
    monkeypatch.setattr("rfp.db.session.create_db_engine", lambda config: engine)
    monkeypatch.setattr("rfp.storage.r2.CloudflareR2Storage", lambda config: Mock())
    monkeypatch.setattr(
        "rfp.ingestion.service.process_job",
        Mock(return_value={"state": "queued", "continuation_required": True}),
    )
    dispatch = Mock()
    monkeypatch.setattr(task, "apply_async", dispatch)
    job_id = str(uuid.uuid4())
    assert task.run(job_id)["state"] == "queued"
    dispatch.assert_called_once_with(args=[job_id], task_id=job_id, countdown=1)
