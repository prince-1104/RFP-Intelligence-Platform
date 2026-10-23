import math

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from rfp.core.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    timeout = math.ceil(settings.dependency_timeout_seconds)
    return create_engine(
        settings.database_url.get_secret_value(),
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
        pool_timeout=timeout,
        connect_args={
            "connect_timeout": timeout,
            "options": f"-c statement_timeout={timeout * 1000}",
        },
    )
