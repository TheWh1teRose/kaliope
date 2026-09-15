"""FastAPI application factory (§2, §11).

One process serves the JSON API, the SSE stream and the built Vue assets.
Startup validates the environment and fails fast with a readable message
(AC-DEP-3), creates the schema when ``/data`` is empty (AC-DEP-2), syncs the
flow catalogue, and starts the worker pool.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, authoring, bench, documents, feedback, folders, review, runs
from app.config import get_settings
from app.db import session_scope
from app.errors import (
    ProblemException,
    http_exception_response,
    problem_response,
    validation_exception_response,
)
from app.migrations import ensure_schema
from app.pipeline import catalogue
from app.pipeline.framework.registry import bootstrap_nodes
from app.schemas.api import HealthOut
from app.worker import worker

VERSION = "0.1.0"

logger = logging.getLogger("kalliope")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
    )


def _sync_flows() -> list[str]:
    """Seed the catalogue from what the build ships, then report what runs."""
    with session_scope() as session:
        catalogue.sync_builtins(session)
        return sorted(catalogue.flows(session))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    _configure_logging(settings.log_level)
    settings.validate_required()
    settings.ensure_dirs()

    bootstrap_nodes()
    ensure_schema()
    flow_ids = _sync_flows()
    logger.info("flows available: %s", ", ".join(flow_ids) or "none")

    if not settings.has_any_llm_key():
        logger.warning(
            "No LLM provider key is configured. Documents will parse, but zone "
            "classification and every generation node will fail."
        )

    worker.start()
    try:
        yield
    finally:
        worker.shutdown(wait=False)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kalliope",
        version=VERSION,
        description="Turns long-form learning documents into grounded podcast scripts.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_exception_handler(ProblemException, problem_response)  # type: ignore[arg-type]
    app.add_exception_handler(HTTPException, http_exception_response)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, validation_exception_response)

    app.include_router(auth.router)
    app.include_router(folders.router)
    app.include_router(documents.router)
    app.include_router(runs.router)
    app.include_router(review.router)
    app.include_router(authoring.router)
    app.include_router(bench.router)
    app.include_router(feedback.router)

    @app.get("/api/health", response_model=HealthOut, tags=["meta"])
    def health() -> HealthOut:
        settings = get_settings()
        with session_scope() as session:
            flows = sorted(catalogue.flows(session))
        return HealthOut(
            status="ok",
            version=VERSION,
            llm_configured=settings.has_any_llm_key(),
            flows=flows,
        )

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built Vue app, with SPA fallback for client-side routes."""
    candidates = [
        Path(os.environ.get("FRONTEND_DIST", "")),
        Path(__file__).resolve().parent / "static",
        Path(__file__).resolve().parents[2] / "frontend" / "dist",
    ]
    dist = next((p for p in candidates if p and p.is_dir() and (p / "index.html").exists()), None)
    if dist is None:
        logger.info("no built frontend found; serving the API only")
        return

    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def spa(path: str) -> FileResponse | JSONResponse:
        if path.startswith("api/"):
            return JSONResponse(
                status_code=404,
                content={"type": "about:blank", "title": "Not found", "status": 404},
                media_type="application/problem+json",
            )
        candidate = dist / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index)

    logger.info("serving the console from %s", dist)


def main() -> None:  # pragma: no cover - entry point
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",  # noqa: S104 - the container is the boundary
        port=8000,
        log_level=settings.log_level.lower(),
    )


app = create_app()
