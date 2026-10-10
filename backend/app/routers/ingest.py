from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import StreamingResponse

from ..config import get_settings
from ..db import get_engine
from ..deps import CurrentUser, get_current_user, require_upload_role
from ..services import behavioral_queries, queries
from ..services.errors import ApiError
from ..services.ingestion import ingest_book

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

Book = Literal["asset", "liability", "historical"]
Severity = Literal["error", "warning"]


@router.post("/{book}", status_code=201)
def upload(
    book: Book,
    file: UploadFile = File(..., description="CSV (UTF-8) or Excel .xlsx"),
    as_of_date: str | None = Form(None, description="YYYY-MM-DD; default: DDMMYYYY from the file name, else today"),
    user: CurrentUser = Depends(require_upload_role),
):
    """Upload one book. Support role only. Runs in the threadpool (plain `def`),
    so a big upload never blocks other requests."""
    limit = get_settings().max_upload_mb * 1024 * 1024
    content = file.file.read(limit + 1)
    if len(content) > limit:
        raise ApiError(413, "FILE_TOO_LARGE", f"File is larger than {get_settings().max_upload_mb} MB",
                       {"max_mb": get_settings().max_upload_mb})
    as_of = None
    if as_of_date not in (None, ""):
        try:
            as_of = date.fromisoformat(as_of_date)
        except ValueError:
            raise ApiError(422, "AS_OF_DATE_INVALID", "as_of_date must be a real date in YYYY-MM-DD format") from None
    result = ingest_book(get_engine(), book=book, content=content, filename=file.filename or "upload",
                         as_of_date=as_of, actor=user.actor)
    behavioral_queries.invalidate()          # a new snapshot: drop cached AI ladder data in this API process
    if get_settings().environment != "test":
        from .behavioral import warm_async      # local import: the AI router imports this module's siblings
        warm_async()                             # recompute the AI views in the background, ready on first visit
    return result


@router.get("/summary")
def get_summary(as_of_date: date | None = None, user: CurrentUser = Depends(get_current_user)):
    return queries.summary(get_engine(), as_of_date)


@router.get("/batches")
def get_batches(limit: int = Query(20, ge=1, le=200), user: CurrentUser = Depends(get_current_user)):
    return {"items": queries.list_batches(get_engine(), limit)}


@router.get("/errors")
def get_errors(
    batch_id: str | None = Query(None, description="one batch id, or a comma separated list"),
    book: Book | None = None,
    severity: Severity | None = None,
    rule_code: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
):
    return queries.list_errors(get_engine(), batch_ids=queries.parse_ids(batch_id), book=book,
                               severity=severity, rule_code=rule_code, page=page, page_size=page_size)


@router.get("/errors.csv")
def get_errors_csv(
    batch_ids: str | None = Query(None, description="comma separated batch ids; omit for everything active"),
    book: Book | None = None,
    severity: Severity | None = None,
    rule_code: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """Same rows, filters, order and columns as GET /errors (the on-screen table), without paging."""
    ids = queries.parse_ids(batch_ids)
    if batch_ids is not None and not ids:
        raise ApiError(422, "BAD_REQUEST", "batch_ids must contain at least one batch id (or be left out)")
    return StreamingResponse(
        queries.errors_csv(get_engine(), batch_ids=ids, book=book, severity=severity, rule_code=rule_code),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="liquidplus_error_report.csv"'})
