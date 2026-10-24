FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TESSERACT_CMD=/usr/bin/tesseract
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv==0.13.5
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable
COPY alembic.ini ./
ENV PATH="/app/.venv/bin:$PATH" SEARCH__MODEL_CACHE_DIR=/app/model-cache
RUN useradd --create-home app && mkdir -p /app/model-cache && chown app:app /app/model-cache
USER app
EXPOSE 8000
CMD ["uvicorn", "rfp.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
