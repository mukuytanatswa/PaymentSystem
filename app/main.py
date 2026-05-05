import asyncio
import logging
import time
from contextlib import asynccontextmanager, suppress

import httpx
import pythonjsonlogger.jsonlogger as jsonlogger
import sentry_sdk
from fastapi import Depends, Request
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.starlette import StarletteIntegration
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy import text

from app.config.database import AsyncSessionLocal, get_db
from app.config.env import settings
from app.config.logging import RequestIdFilter
from app.middleware.auth import ApiKeyMiddleware
from app.middleware.error_handler import global_exception_handler, rate_limit_handler
from app.middleware.rate_limiter import limiter
from app.middleware.request_id import RequestIdMiddleware
from app.middleware.request_logger import RequestLoggerMiddleware
from app.routes import admin, payments, reconciliation, vendors, webhooks
from app.services.reconciliation_service import start_reconciliation_worker
from app.services.retention_service import start_retention_worker
from app.services.retry_service import start_retry_worker

if settings.sentry_dsn:
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        integrations=[StarletteIntegration(), FastApiIntegration()],
        traces_sample_rate=0.1,
    )

# Structured JSON logging
_handler = logging.StreamHandler()
_handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(name)s %(levelname)s %(request_id)s %(message)s"))
logging.root.setLevel(logging.INFO)
logging.root.addHandler(_handler)
logging.root.addFilter(RequestIdFilter())


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.startup_time = time.time()
    app.state.retry_task = asyncio.create_task(start_retry_worker())
    app.state.reconciliation_task = asyncio.create_task(start_reconciliation_worker())
    app.state.retention_task = asyncio.create_task(start_retention_worker())
    yield
    for task in (app.state.retry_task, app.state.reconciliation_task, app.state.retention_task):
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="QuickPayments", version="1.0.0", lifespan=lifespan)

# Rate limiter state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_handler)
app.add_middleware(SlowAPIMiddleware)

# Auth middleware (runs after rate limiter)
app.add_middleware(ApiKeyMiddleware)

# Request logger — runs between RequestId and ApiKey; logs after response so platform_id is available
app.add_middleware(RequestLoggerMiddleware)

# Request ID — added last so it runs first (Starlette reverses registration order)
app.add_middleware(RequestIdMiddleware)

# Global error handler
app.add_exception_handler(Exception, global_exception_handler)

# Routers
app.include_router(payments.router)
app.include_router(vendors.router)
app.include_router(webhooks.router)
app.include_router(reconciliation.router)
app.include_router(admin.router)


@app.get("/health", tags=["health"])
async def health(request: Request):
    checks: dict = {}

    # Database
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "connected"
    except Exception:
        checks["database"] = "error"

    # Stitch reachability
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get("https://api.stitch.money")
        checks["stitch"] = "reachable" if r.status_code < 500 else "degraded"
    except Exception:
        checks["stitch"] = "unreachable"

    # Background workers
    retry_task: asyncio.Task = request.app.state.retry_task
    checks["retry_service"] = "stopped" if retry_task.done() else "running"
    recon_task: asyncio.Task = request.app.state.reconciliation_task
    checks["reconciliation_service"] = "stopped" if recon_task.done() else "running"
    retention_task: asyncio.Task = request.app.state.retention_task
    checks["retention_service"] = "stopped" if retention_task.done() else "running"

    checks["uptime"] = int(time.time() - request.app.state.startup_time)
    checks["status"] = (
        "ok"
        if all(v not in ("error", "unreachable", "stopped") for v in checks.values() if isinstance(v, str))
        else "degraded"
    )

    return JSONResponse(
        content=checks,
        status_code=200 if checks["status"] == "ok" else 503,
    )
