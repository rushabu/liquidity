import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..db import check_connection

router = APIRouter(tags=["management"])
log = logging.getLogger("liquidplus.health")


@router.get("/health")
def health():
    s = get_settings()
    return {"status": "ok", "service": s.app_name, "environment": s.environment}


@router.get("/health/db")
def health_db():
    t0 = time.perf_counter()
    try:
        check_connection()
    except Exception as e:
        log.error("database health check failed", extra={"error": e.__class__.__name__})
        return JSONResponse(status_code=503, content={"status": "down", "database": "unreachable"})
    return {"status": "ok", "database": "reachable",
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}
