"""FastAPI application entry point.  Run:  uvicorn backend.main:app --port 8000"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .approvals.routes import router as approvals_router
from .audit.routes import router as audit_router
from .auth.routes import router as auth_router
from .config import settings
from .database import close_pools, db_is_healthy, init_pools
from .employees_routes import router as employees_router
from .excel.routes import router as excel_router
from .export_routes import router as export_router
from .query_routes import router as query_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("app")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.validate()
    try:
        init_pools()
    except Exception:  # noqa: BLE001 — start anyway; requests will report the outage cleanly
        log.exception("PostgreSQL is not reachable at startup")
    yield
    close_pools()


app = FastAPI(title="AI Database Work Assistant", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")

for r in (auth_router, query_router, approvals_router, excel_router, employees_router, export_router, audit_router):
    app.include_router(r)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.get("/api/health")
def health():
    return {"status": "ok", "database": db_is_healthy()}


# ───────── safe error handling: never leak stack traces ─────────
@app.exception_handler(StarletteHTTPException)
async def http_exc(_: Request, exc: StarletteHTTPException):
    msg = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(status_code=exc.status_code, content={"success": False, "message": msg})


@app.exception_handler(RequestValidationError)
async def validation_exc(_: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"success": False, "message": "The request was not valid. "
                                                  "Please check your input."})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    if exc.__class__.__module__.startswith(("psycopg", "psycopg_pool")):
        return JSONResponse(status_code=503, content={"success": False,
                            "message": "Database connection failed. Please try again later."})
    return JSONResponse(status_code=500, content={"success": False,
                        "message": "Something went wrong. Please try again later."})
