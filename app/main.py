from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import router as api_router
from app.api.v1.webhooks import router as webhook_router
from app.config import Settings
from app.provisioning.netbox_bootstrap import bootstrap_netbox
from app.provisioning.twenty_bootstrap import bootstrap_twenty

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

logger = logging.getLogger("netbox_twenty.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = Settings()
    logger.info("Starting NetBox ↔ Twenty middleware")

    # Run provisioning engine
    try:
        await bootstrap_netbox(settings)
    except Exception:
        logger.exception("NetBox provisioning failed – continuing startup")

    try:
        await bootstrap_twenty(settings)
    except Exception:
        logger.exception("Twenty CRM provisioning failed – continuing startup")

    logger.info("Provisioning complete, middleware ready")
    yield
    logger.info("Shutting down middleware")


app = FastAPI(
    title="NetBox ↔ Twenty CRM Middleware",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)
app.include_router(webhook_router)


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next: object) -> Response:
    start = time.perf_counter()
    response: Response = await call_next(request)  # type: ignore[reportGeneralTypeIssues]
    elapsed_ms = (time.perf_counter() - start) * 1000
    client_ip = request.client.host if request.client else "unknown"
    logger.info(
        "request completed",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        elapsed_ms=f"{elapsed_ms:.1f}",
        client_ip=client_ip,
    )
    return response
