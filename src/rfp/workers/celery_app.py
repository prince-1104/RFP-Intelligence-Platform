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

    return app
