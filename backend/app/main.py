"""FastAPI Application Entry Point for Colab Management Platform.

Mounts authentication, account, session, terminal WebSocket, and file explorer routers.
Serves production React SPA on port 1113 with client-side routing fallback.
"""

from __future__ import annotations

import contextlib
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.routes_accounts import router as accounts_router
from backend.app.api.routes_auth import router as auth_router
from backend.app.api.routes_files import router as files_router
from backend.app.api.routes_jobs import router as jobs_router
from backend.app.api.routes_notifications import router as notifications_router
from backend.app.api.routes_sessions import router as sessions_router
from backend.app.api.routes_storage import router as storage_router
from backend.app.api.routes_telemetry import router as telemetry_router
from backend.app.api.routes_templates import router as templates_router
from backend.app.api.routes_terminal import router as terminal_router
from backend.app.config import settings
from backend.app.core.database import check_db_health, init_db
from backend.app.core.security import get_master_key

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("colab_platform")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle startup and shutdown hooks."""
    logger.info("Initializing Colab Management Platform on port %s...", settings.PORT)
    # Ensure Master Key is loaded or generated
    get_master_key()
    # Ensure SQLite WAL database and schema are initialized
    init_db()
    logger.info("Database and Master Key Vault initialized successfully.")
    # Initialize SSH Multiplexer listeners for active sessions
    try:
        from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
        await ssh_multiplexer_service.init_from_db()
    except Exception as e:
        logger.warning("Could not initialize SSH multiplexer listeners: %s", e)

    # Start Phase 3 services (Lifecycle Engine, Telemetry, Job Runner)
    try:
        from backend.app.services.lifecycle_engine import lifecycle_engine
        await lifecycle_engine.start()
    except Exception as e:
        logger.warning("Could not start lifecycle engine: %s", e)

    try:
        from backend.app.services.telemetry import telemetry_service
        await telemetry_service.start()
    except Exception as e:
        logger.warning("Could not start telemetry service: %s", e)

    try:
        from backend.app.services.job_runner import job_runner_service
        await job_runner_service.start()
    except Exception as e:
        logger.warning("Could not start job runner service: %s", e)

    yield

    # Shutdown Phase 3 services
    try:
        from backend.app.services.job_runner import job_runner_service
        await job_runner_service.stop()
    except Exception as e:
        logger.warning("Error stopping job runner: %s", e)

    try:
        from backend.app.services.telemetry import telemetry_service
        await telemetry_service.stop()
    except Exception as e:
        logger.warning("Error stopping telemetry service: %s", e)

    try:
        from backend.app.services.lifecycle_engine import lifecycle_engine
        await lifecycle_engine.stop()
    except Exception as e:
        logger.warning("Error stopping lifecycle engine: %s", e)

    # Shutdown SSH multiplexer gateways
    try:
        from backend.app.services.ssh_multiplexer import ssh_multiplexer_service
        await ssh_multiplexer_service.shutdown()
    except Exception as e:
        logger.warning("Error shutting down SSH multiplexer: %s", e)
    logger.info("Shutting down Colab Management Platform.")


app = FastAPI(
    title="Colab Management Platform",
    version="1.0.0",
    description="Multi-account Google Colab fleet management, terminal proxy, and automation engine.",
    lifespan=lifespan,
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount API Routers
app.include_router(auth_router)
app.include_router(accounts_router)
app.include_router(sessions_router)
app.include_router(terminal_router)
app.include_router(files_router)
app.include_router(templates_router)
app.include_router(storage_router)
app.include_router(telemetry_router)
app.include_router(jobs_router)
app.include_router(notifications_router)


# Health Check Endpoint for Docker & Orchestrator
@app.get("/healthz", tags=["health"])
async def health_check():
    """Health check endpoint verifying process vitality and database connectivity."""
    db_status = check_db_health()
    return {
        "status": "ok",
        "service": "colab-management-backend",
        "port": settings.PORT,
        "database": db_status,
    }


# Static Frontend SPA Serving & Fallback
static_dir = Path(settings.STATIC_DIR)
index_file = static_dir / "index.html"

# If assets dir exists, mount it directly
assets_dir = static_dir / "assets"
if assets_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")


@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str):
    """Catch-all route serving static frontend assets with client-side SPA fallback to index.html."""
    clean_path = full_path.lstrip("/")
    if clean_path.startswith("api/") or clean_path == "api" or clean_path == "healthz":
        raise HTTPException(status_code=404, detail="API route not found")

    try:
        resolved_static = static_dir.resolve()
        resolved_target = (static_dir / clean_path).resolve()
        if resolved_target.is_relative_to(resolved_static) and resolved_target.is_file():
            return FileResponse(str(resolved_target))
    except (ValueError, RuntimeError):
        pass

    if index_file.is_file():
        return FileResponse(str(index_file))

    # Fallback if frontend has not yet been built
    return Response(
        content="<html><body><h1>Colab Management Platform</h1><p>Frontend build pending.</p></body></html>",
        media_type="text/html",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host=settings.HOST, port=settings.PORT, reload=False)
