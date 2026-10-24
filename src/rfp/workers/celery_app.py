from celery import Celery

from rfp.core.config import Settings, get_settings


def create_celery(settings: Settings | None = None) -> Celery:
    settings = settings or get_settings()
    app = Celery("rfp", broker=settings.redis_url.get_secret_value())
    app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        task_ignore_result=True,
        worker_prefetch_multiplier=1,
        task_time_limit=60,
        task_soft_time_limit=50,
        broker_connection_timeout=3,
        broker_connection_retry_on_startup=True,
        broker_transport_options={"visibility_timeout": 3600},
    )

    @app.task(name="rfp.ping")
    def ping() -> str:
        """Diagnostic only; never creates business records."""
        return "pong"

    @app.task(
        bind=True,
        name="rfp.ingest",
        max_retries=3,
        acks_late=True,
        soft_time_limit=settings.parser_timeout_seconds + 570,
        time_limit=settings.parser_timeout_seconds + 600,
    )
    def ingest(self, job_id: str) -> dict:
        import uuid

        from rfp.db.session import create_db_engine
        from rfp.ingestion.service import IngestionBusy, process_job
        from rfp.storage.r2 import CloudflareR2Storage, TransientStorageError

        engine = create_db_engine(settings)
        try:
            result = process_job(
                uuid.UUID(job_id),
                settings,
                engine,
                CloudflareR2Storage(settings),
                final_attempt=self.request.retries >= self.max_retries,
            )
            if result.get("continuation_required"):
                self.apply_async(args=[job_id], task_id=job_id, countdown=1)
            return result
        except (TransientStorageError, IngestionBusy) as exc:
            raise self.retry(
                exc=type(exc)("ingestion_retry"), countdown=min(2**self.request.retries * 5, 60)
            ) from None
        finally:
            engine.dispose()

    @app.task(
        bind=True,
        name="rfp.chunk",
        max_retries=3,
        acks_late=True,
        soft_time_limit=settings.chunking.timeout_seconds + settings.chunking.batch_seconds + 300,
        time_limit=settings.chunking.timeout_seconds + settings.chunking.batch_seconds + 330,
    )
    def chunk(self, job_id: str) -> dict:
        import uuid

        from rfp.chunking.service import process_chunks
        from rfp.db.session import create_db_engine
        from rfp.ingestion.service import IngestionBusy
        from rfp.storage.r2 import CloudflareR2Storage, TransientStorageError

        engine = create_db_engine(settings)
        try:
            result = process_chunks(
                uuid.UUID(job_id),
                settings,
                engine,
                CloudflareR2Storage(settings),
                final_attempt=self.request.retries >= self.max_retries,
            )
            if result.get("continuation_required"):
                self.apply_async(args=[job_id], task_id=job_id, countdown=1)
            return result
        except (TransientStorageError, IngestionBusy) as exc:
            raise self.retry(
                exc=type(exc)("chunk_retry"), countdown=min(2**self.request.retries * 5, 60)
            ) from None
        finally:
            engine.dispose()

    @app.task(
        bind=True,
        name="rfp.index",
        max_retries=3,
        acks_late=True,
        soft_time_limit=settings.search.index_batch_seconds + 600,
        time_limit=settings.search.index_batch_seconds + 660,
    )
    def index(self, job_id: str) -> dict:
        import uuid

        from rfp.core.logging import request_id
        from rfp.db.session import create_db_engine
        from rfp.ingestion.service import IngestionBusy
        from rfp.search.jobs import IndexRetryPending, process_index_job
        from rfp.search.wiring import production_indexing_service

        token = request_id.set(job_id.replace("-", "")[:64])
        engine = create_db_engine(settings)
        try:
            result = process_index_job(
                uuid.UUID(job_id),
                settings,
                engine,
                lambda: production_indexing_service(settings, engine),
                final_attempt=self.request.retries >= self.max_retries,
            )
            if result.get("continuation_required"):
                self.apply_async(args=[job_id], task_id=job_id, countdown=1)
            return result
        except (IndexRetryPending, IngestionBusy) as exc:
            raise self.retry(
                exc=type(exc)("index_retry"), countdown=min(2**self.request.retries * 5, 60)
            ) from None
        finally:
            engine.dispose()
            request_id.reset(token)

    return app
