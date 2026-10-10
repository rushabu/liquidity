"""One upload = one database transaction.

  parse -> validate -> [ lock -> create batch -> COPY rows (chunks of 5,000)
  -> COPY errors -> deactivate the previous active batch for the same
  (book, as_of_date) -> audit row ] -> commit

Any failure rolls everything back (no half-loaded data, no new active batch).
File-level problems (wrong type, missing columns, ...) raise ApiError(422)
before the database is touched.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Iterable, Iterator, Sequence

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..config import get_settings
from .errors import ApiError
from .parsing import as_of_from_filename, parse_file
from .validation import Issue, validate

log = logging.getLogger("liquidplus.ingestion")

_COMMON = ["transaction_id", "entity", "location", "customer_id", "currency", "transaction_date",
           "interest_rate_pct", "market_rate_pct", "customer_segment", "client_rating"]
_LIVE_TAIL = ["tenor_days", "interest_amount", "cash_at_maturity"]

TABLES: dict[str, tuple[str, list[str]]] = {
    "asset": ("assets", _COMMON + ["loan_amount", "maturity_date", "loan_type"] + _LIVE_TAIL),
    "liability": ("liabilities", _COMMON + ["amount", "maturity_date", "product_type"] + _LIVE_TAIL),
    "historical": ("historical_log", _COMMON + ["amount", "type", "product_type",
                                                "contractual_maturity_date", "actual_closure_date",
                                                "is_early_exit"]),
}
ERROR_COLUMNS = ["source_row", "transaction_id", "field", "value", "rule_code", "message", "severity"]


def _safe(s: str | None) -> str | None:
    """Postgres text cannot hold NUL, and COPY needs valid UTF-8."""
    if s is None:
        return None
    return s.replace("\x00", "").encode("utf-8", "replace").decode("utf-8")


def _chunks(items: Sequence, size: int) -> Iterator[Sequence]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _copy(cur, table: str, columns: list[str], rows: Iterable[Sequence]) -> None:
    """Bulk load through COPY. Kept as one small function so tests can inject failures."""
    with cur.copy(f"COPY {table} ({', '.join(columns)}) FROM STDIN") as cp:
        for r in rows:
            cp.write_row(r)


def _issue_row(batch_id: str, i: Issue) -> tuple:
    return (batch_id, i.source_row, _safe(i.transaction_id), _safe(i.field), _safe(i.value),
            i.rule_code, _safe(i.message), i.severity)


def ingest_book(engine: Engine, *, book: str, content: bytes, filename: str,
                as_of_date: date | None, actor: str) -> dict:
    settings = get_settings()
    t0 = time.perf_counter()
    filename = _safe(filename) or "upload"

    try:
        parsed = parse_file(content, filename)
        result = validate(book, parsed)
    except ApiError as e:
        log.warning("ingestion rejected", extra={"book": book, "upload_filename": filename, "code": e.code,
                                                 "actor": actor})
        _audit_best_effort(engine, actor, "INGEST_REJECTED",
                           {"book": book, "filename": filename, "code": e.code, "message": e.message})
        raise

    as_of = as_of_date or as_of_from_filename(filename) or date.today()
    table, columns = TABLES[book]
    size = settings.insert_chunk_size

    with engine.begin() as conn:       # commit on success, rollback on ANY exception
        # serialise concurrent uploads of the same (book, snapshot date)
        conn.execute(text("select pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"{book}:{as_of}"})

        batch_id = str(conn.execute(text("""
            insert into ingestion_batches
              (book, as_of_date, source_filename, uploaded_by, status, rows_read, rows_valid,
               rows_rejected, warnings_count, is_active)
            values (:book, :as_of, :fn, :actor, 'completed', :read, :valid, :rej, :warn, true)
            returning id"""), {
            "book": book, "as_of": as_of, "fn": filename, "actor": actor, "read": result.rows_read,
            "valid": len(result.valid_rows), "rej": result.rows_rejected, "warn": len(result.warnings),
        }).scalar_one())

        cur = conn.connection.cursor()
        try:
            cols = ["batch_id"] + columns
            rows = [(batch_id, *[r.get(c) for c in columns]) for r in result.valid_rows]
            for chunk in _chunks(rows, size):
                _copy(cur, table, cols, chunk)

            issue_rows = [_issue_row(batch_id, i) for i in result.issues]
            for chunk in _chunks(issue_rows, size):
                _copy(cur, "ingestion_errors", ["batch_id"] + ERROR_COLUMNS, chunk)
        finally:
            cur.close()

        replaced = conn.execute(text("""
            update ingestion_batches set is_active = false
             where book = :book and as_of_date = :as_of and is_active and id <> :id
            returning id"""), {"book": book, "as_of": as_of, "id": batch_id}).scalars().all()

        duration_ms = round((time.perf_counter() - t0) * 1000)
        conn.execute(text("update ingestion_batches set duration_ms = :d where id = :id"),
                     {"d": duration_ms, "id": batch_id})
        summary = {
            "batch_id": batch_id, "book": book, "as_of_date": as_of.isoformat(), "filename": filename,
            "rows_read": result.rows_read, "rows_valid": len(result.valid_rows),
            "rows_rejected": result.rows_rejected, "warnings": len(result.warnings),
            "duration_ms": duration_ms,
            "replaced_batch_id": str(replaced[0]) if replaced else None,
        }
        conn.execute(text("insert into audit_log (actor, action, details) values (:a, 'INGEST_UPLOAD', "
                          "cast(:d as jsonb))"), {"a": actor, "d": json.dumps(summary)})

    summary["errors_preview"] = [
        {"source_row": i.source_row, "transaction_id": i.transaction_id, "field": i.field, "value": i.value,
         "rule_code": i.rule_code, "message": i.message, "severity": i.severity}
        for i in result.issues[: settings.errors_preview_limit]]
    log.info("ingestion completed", extra={
        "actor": actor, "book": book, "upload_filename": filename, "as_of_date": as_of.isoformat(),
        "batch_id": batch_id, "rows_read": result.rows_read, "rows_valid": len(result.valid_rows),
        "rows_rejected": result.rows_rejected, "warnings": len(result.warnings), "duration_ms": duration_ms})
    return summary


def _audit_best_effort(engine: Engine, actor: str, action: str, details: dict) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text("insert into audit_log (actor, action, details) values (:a, :act, cast(:d as jsonb))"),
                         {"a": actor, "act": action, "d": json.dumps(details, default=str)})
    except Exception:       # auditing a rejected file must never mask the real error
        log.warning("could not write audit row", exc_info=True)
