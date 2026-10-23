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
        with httpx.Client(timeout=settings.dependency_timeout_seconds, trust_env=False) as client:
            client.get(f"{settings.qdrant_url}/readyz").raise_for_status()

    return {"postgres": postgres, "redis": redis, "qdrant": qdrant}
