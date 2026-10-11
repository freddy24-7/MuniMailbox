"""Rules of the codebase, checked by reading the code. A change that breaks one of these
fails here even if every behavioural test still passes."""

import ast
from collections.abc import Iterable, Iterator
from pathlib import Path

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.auth.dependencies import current_user
from app.main import app

APP_DIR = Path(__file__).resolve().parents[2] / "app"
# /metrics has no user (Prometheus cannot log in); it can require a token instead.
PUBLIC_PATHS = {"/healthz", "/readyz", "/metrics", "/login", "/login/{user_id}"}


def _imports(path: Path) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_only_the_router_and_the_wiring_touch_model_clients() -> None:
    """CLAUDE.md rule 1: never call a model client directly from an agent."""
    allowed = {"inference/clients.py", "inference/router.py", "services.py", "main.py"}
    offenders = [
        str(path.relative_to(APP_DIR))
        for path in APP_DIR.rglob("*.py")
        if str(path.relative_to(APP_DIR)) not in allowed
        and "app.inference.clients" in _imports(path)
    ]
    assert offenders == []


def test_agents_do_not_import_http_libraries() -> None:
    for path in (APP_DIR / "agents").glob("*.py"):
        assert not {"httpx", "openai", "requests"} & _imports(path), path.name


def test_outbox_messages_are_created_in_one_place_only() -> None:
    """CLAUDE.md rule 3: one code path into the outbox."""
    creators = []
    for path in APP_DIR.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "OutboxMessage"
            ):
                creators.append(str(path.relative_to(APP_DIR)))
    assert creators == ["review/service.py"]


def _depends_on_login(dependant: Dependant) -> bool:
    return dependant.call is current_user or any(
        _depends_on_login(child) for child in dependant.dependencies
    )


def _api_routes(routes: Iterable[object]) -> Iterator[APIRoute]:
    for route in routes:
        if isinstance(route, APIRoute):
            yield route
        # FastAPI keeps an included router as one entry that wraps the original router.
        elif (included := getattr(route, "original_router", None)) is not None:
            yield from _api_routes(included.routes)


def test_every_endpoint_requires_a_logged_in_user_unless_listed_as_public() -> None:
    """GR-5: a new endpoint that forgets its role check fails this test."""
    unprotected = {
        route.path for route in _api_routes(app.routes) if not _depends_on_login(route.dependant)
    }
    # Equality, not subset: it also fails if this test stops finding the routes at all.
    assert unprotected == PUBLIC_PATHS
