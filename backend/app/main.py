from __future__ import annotations

import logging
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .logging_setup import request_id_var, setup_logging
from .routers import health, ingest
from .services.errors import ApiError

settings = get_settings()
setup_logging(settings.log_level)
log = logging.getLogger("liquidplus")

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                   allow_headers=["*"], expose_headers=["x-request-id", "content-disposition"])


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
    token = request_id_var.set(rid)
    t0 = time.perf_counter()
    try:
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        log.info("request", extra={"method": request.method, "path": request.url.path,
                                   "status": response.status_code,
                                   "ms": round((time.perf_counter() - t0) * 1000)})
        return response
    finally:
        request_id_var.reset(token)


def _err(status: int, code: str, message: str, details=None) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {
        "code": code, "message": message, "details": details or {}}},
        headers={"x-request-id": request_id_var.get()})


@app.exception_handler(ApiError)
async def api_error(_: Request, e: ApiError):
    return _err(e.status, e.code, e.message, e.details)


@app.exception_handler(HTTPException)
async def http_error(_: Request, e: HTTPException):
    return _err(e.status_code, {401: "UNAUTHORIZED", 403: "FORBIDDEN", 404: "NOT_FOUND"}.get(
        e.status_code, "HTTP_ERROR"), str(e.detail))


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, e: RequestValidationError):
    return _err(422, "BAD_REQUEST", "Invalid request parameters",
                {"errors": [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()]})


@app.exception_handler(Exception)
async def unhandled(_: Request, e: Exception):
    rid = request_id_var.get()
    log.exception("unhandled error")
    return _err(500, "INTERNAL_ERROR", f"Unexpected server error. Quote correlation id {rid}.",
                {"correlation_id": rid})


app.include_router(health.router)
app.include_router(ingest.router)
