"""API FastAPI do Sub-Roga (contrato em docs/API.md). Subir: `uv run uvicorn app.main:app`."""
from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.engine import Engine
from sqlmodel import select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings, load_dotenv
from app.models import Case
from app.pipeline.run import Steps, default_steps
from app.routers import auth as auth_routes
from app.routers import cases as case_routes
from app.routers import public as public_routes
from app.routers.deps import Ctx

load_dotenv()
log = logging.getLogger(__name__)

_TRANSLATED = {"Not Found": "Rota não encontrada.", "Method Not Allowed": "Método não permitido."}


def _health() -> dict[str, Any]:
    try:
        from app.pipeline.llm import is_ready, provider_name

        return {"ok": True, "llm_provider": provider_name(), "llm_ready": bool(is_ready())}
    except Exception:  # noqa: BLE001
        return {"ok": True, "llm_provider": "unknown", "llm_ready": False}


def _validation_message(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(x) for x in err.get("loc", ()) if x not in ("body", "query", "path"))
        parts.append(f"{loc or 'requisição'}: {err.get('msg', 'inválido')}")
    return "Dados inválidos — " + "; ".join(parts)


def _resume_stuck_cases(ctx: Ctx) -> None:
    """Casos que ficaram em PROCESSANDO (servidor reiniciado) voltam para a fila."""
    with ctx.session() as s:
        ids = [c.id for c in s.exec(select(Case).where(Case.status == "PROCESSANDO")).all()]
    for case_id in ids:
        threading.Thread(target=ctx.run_pipeline, args=(case_id,), daemon=True).start()


def _mount_frontend(app: FastAPI, frontend: Path) -> None:
    index = frontend / "index.html"
    if not index.is_file():
        return
    if (frontend / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")
    root = frontend.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str) -> FileResponse:
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, "Rota não encontrada.")
        candidate = (root / full_path).resolve()
        if full_path and candidate.is_file() and candidate.is_relative_to(root):
            return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})


def create_app(engine: Engine | None = None, storage_dir: Path | None = None,
               steps_factory: Callable[[], Steps] = default_steps,
               today: Callable[[], date] = date.today, settings: Settings | None = None,
               resume_on_startup: bool = True) -> FastAPI:
    settings = settings or get_settings()
    if storage_dir is not None:
        settings.storage_dir = Path(storage_dir)
    ctx = Ctx(settings=settings, steps_factory=steps_factory, today=today, engine=engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        ctx.get_engine()  # cria o banco/tabelas no startup, não no import
        if resume_on_startup:
            _resume_stuck_cases(ctx)
        yield

    app = FastAPI(title="Sub-Roga", lifespan=lifespan)
    app.state.ctx = ctx
    cors: dict[str, Any] = {"allow_origins": settings.cors_origins} if settings.cors_origins else {
        "allow_origin_regex": r"https?://(localhost|127\.0\.0\.1)(:\d+)?"}
    app.add_middleware(CORSMiddleware, allow_methods=["*"], allow_headers=["*"],
                       expose_headers=["X-Narrative", "Content-Disposition"], **cors)

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": _validation_message(exc)}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = _TRANSLATED.get(str(exc.detail), exc.detail)
        return JSONResponse({"detail": detail}, status_code=exc.status_code, headers=exc.headers)

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return _health()

    app.include_router(auth_routes.router)
    app.include_router(case_routes.router)
    app.include_router(public_routes.router)
    _mount_frontend(app, settings.frontend_dir)
    return app


app = create_app()
