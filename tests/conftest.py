import pytest

from rfp.core.config import Settings


@pytest.fixture
def settings():
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@localhost/test",
        redis_url="redis://localhost:6379/0",
        qdrant_url="http://localhost:6333",
        # Offline tests use the deterministic estimate; real-tokenizer tests opt in explicitly.
        chunking={"token_counter": "utf8_bytes_v1", "tokenizer_identifier": "utf8-bytes-div4-v1"},
    )
