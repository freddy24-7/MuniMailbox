from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.api import health
from app.auth.dependencies import NotAuthenticatedError
from app.config import Settings, get_settings
from app.inference.clients import ChatClient
from app.observability.correlation import CorrelationIdMiddleware
from app.observability.logging import configure_logging
from app.privacy.redaction import Redactor
from app.services import build_services
from app.web import routes as web

STATIC_DIR = Path(__file__).parent / "web" / "static"


def create_app(
    settings: Settings | None = None,
    *,
    local_llm: ChatClient | None = None,
    redactor: Redactor | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.services = await build_services(settings, local_llm=local_llm, redactor=redactor)
        yield
        await app.state.services.aclose()

    app = FastAPI(title="Municipal Inbox Assistant", lifespan=lifespan)
    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(health.router)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.exception_handler(NotAuthenticatedError)
    async def to_login(request: Request, _: NotAuthenticatedError) -> RedirectResponse:
        return RedirectResponse("/login", status_code=303)

    return app


app = create_app()
