import pytest

from rfp.core.config import Settings


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@localhost/test",
        redis_url="redis://localhost:6379/0",
        qdrant_url="http://localhost:6333",
    )
