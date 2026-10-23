from collections.abc import Callable

import httpx
from redis import Redis
from sqlalchemy import text
from sqlalchemy.engine import Engine

from rfp.core.config import Settings


def dependency_checks(settings: Settings, engine: Engine) -> dict[str, Callable[[], None]]:
    def postgres() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def redis() -> None:
        with Redis.from_url(
            settings.redis_url.get_secret_value(),
            socket_connect_timeout=settings.dependency_timeout_seconds,
            socket_timeout=settings.dependency_timeout_seconds,
        ) as client:
            client.ping()

    def qdrant() -> None:
        api_key = settings.qdrant_api_key.get_secret_value()
        headers = {"api-key": api_key} if api_key else {}
        with httpx.Client(timeout=settings.dependency_timeout_seconds, trust_env=False) as client:
            client.get(f"{settings.qdrant_url}/readyz", headers=headers).raise_for_status()
            if api_key:
                # Health routes may be public; also verify authenticated cluster access.
                client.get(f"{settings.qdrant_url}/collections", headers=headers).raise_for_status()

    return {"postgres": postgres, "redis": redis, "qdrant": qdrant}
