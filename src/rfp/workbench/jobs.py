"""In-process jobs for ingest, extraction and evaluation. Status is queued, running,
succeeded or failed. There is no synthetic progress."""

import json
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from rfp.workbench.errors import WorkbenchError


class JobStore:
    def __init__(self, workers: int = 1, directory: Path | None = None):
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._directory = directory
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="workbench")

    def submit(self, kind: str, function: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        job_id = uuid.uuid4().hex
        record = {
            "id": job_id,
            "kind": kind,
            "status": "queued",
            "created_at": datetime.now(UTC).isoformat(),
            "ended_at": None,
            "error": None,
            "result": None,
        }
        with self._lock:
            self._jobs[job_id] = record
            self._write(job_id)
        self._pool.submit(self._run, job_id, function)
        return dict(record)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._jobs.get(job_id)
        if record is None:
            raise WorkbenchError("job_not_found", "No workbench job has that id.", 404)
        return dict(record)

    def latest(self, kind: str) -> dict[str, Any] | None:
        with self._lock:
            matches = [item for item in self._jobs.values() if item["kind"] == kind]
        if not matches:
            return None
        return dict(max(matches, key=lambda item: item["created_at"]))

    def _run(self, job_id: str, function: Callable[[], dict[str, Any]]) -> None:
        self._update(job_id, status="running")
        try:
            result = function()
        except WorkbenchError as exc:
            self._update(job_id, status="failed", error={"code": exc.code, "message": exc.message})
        except Exception:
            self._update(
                job_id,
                status="failed",
                error={
                    "code": "operation_failed",
                    "message": "The job failed. Check the local index and settings.",
                },
            )
        else:
            self._update(job_id, status="succeeded", result=result)

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            record = self._jobs[job_id]
            record.update(changes)
            if changes.get("status") in {"succeeded", "failed"}:
                record["ended_at"] = datetime.now(UTC).isoformat()
        self._write(job_id)

    def _write(self, job_id: str) -> None:
        if self._directory is None:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        path = self._directory / f"{job_id}.json"
        path.write_text(json.dumps(self._jobs[job_id], indent=2), encoding="utf-8")
