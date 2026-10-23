import logging
import re
import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from rfp.api.health import dependency_checks
from rfp.core.config import Settings, get_settings
from rfp.core.logging import configure_logging, request_id
from rfp.db.session import create_db_engine

logger = logging.getLogger("rfp.api")


def create_app(
    settings: Settings | None = None, checks: dict[str, Callable[[], None]] | None = None
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings)
    health_checks = dependency_checks(settings, engine) if checks is None else checks

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        engine.dispose()

    app = FastAPI(title="RFP Intelligence Platform", version="0.1.0", lifespan=lifespan)

    def error_response(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"error": {"code": code, "message": message, "request_id": request_id.get()}},
        )

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        supplied = request.headers.get("X-Request-ID", "")
        correlation = (
            supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else uuid.uuid4().hex
        )
        token = request_id.set(correlation)
        try:
            try:
                response = await call_next(request)
            except Exception:
                logger.error("request_failed")
                response = error_response(500, "internal_error", "Internal server error")
            response.headers["X-Request-ID"] = correlation
            logger.info("request_completed")
            return response
        finally:
            request_id.reset(token)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        response = error_response(exc.status_code, "http_error", "Request could not be completed")
        if exc.headers:
            response.headers.update(exc.headers)
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return error_response(422, "validation_error", "Invalid request")

    @app.get("/health/live")
    def live():
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready():
        dependencies = {}
        for name, check in health_checks.items():
            try:
                check()
                dependencies[name] = "healthy"
            except Exception:
                dependencies[name] = "unhealthy"
        healthy = all(state == "healthy" for state in dependencies.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ready" if healthy else "not_ready",
                "dependencies": dependencies,
                "optional": {
                    "r2": "configured_not_checked" if settings.r2_configured else "not_configured",
                    "llm": "not_checked",
                },
            },
        )

    return app
