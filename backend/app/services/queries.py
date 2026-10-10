"""Read-side queries for the ingestion page (batches, summary, errors, CSV)."""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .errors import ApiError

BOOKS = ("asset", "liability", "historical")
BATCH_COLS = ("id, book, as_of_date, source_filename, uploaded_by, status, rows_read, rows_valid, "
              "rows_rejected, warnings_count, duration_ms, is_active, created_at")


def parse_ids(raw: str | None) -> list[str]:
    ids: list[str] = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(str(uuid.UUID(part)))
        except ValueError:
            raise ApiError(422, "BAD_REQUEST", f"'{part}' is not a valid batch id") from None
    return ids


def _row(r) -> dict:
    d = dict(r)
    d["id"] = str(d["id"])
    return d


def list_batches(engine: Engine, limit: int) -> list[dict]:
    with engine.connect() as conn:
        rows = conn.execute(text(f"select {BATCH_COLS} from ingestion_batches "
                                 "order by created_at desc limit :n"), {"n": limit}).mappings().all()
    return [_row(r) for r in rows]


def summary(engine: Engine, as_of: date | None) -> dict:
    """Latest ACTIVE batch per book (optionally for one snapshot date)."""
    with engine.connect() as conn:
        rows = conn.execute(text(f"""
            select distinct on (book) {BATCH_COLS} from ingestion_batches
             where is_active and status = 'completed' and (cast(:d as date) is null or as_of_date = :d)
             order by book, as_of_date desc, created_at desc"""), {"d": as_of}).mappings().all()
    books: dict[str, dict | None] = {b: None for b in BOOKS}
    for r in rows:
        books[r["book"]] = _row(r)
    present = [b for b in books.values() if b]
    dates = {b["as_of_date"] for b in present}
    return {
        "as_of_date": max(dates).isoformat() if dates else None,
        "books": books,
        "rows_stored_total": sum(b["rows_valid"] for b in present),
        # the ladder needs all three books for the SAME snapshot date
        "ready": len(present) == len(BOOKS) and len(dates) == 1,
    }


def _scope(batch_ids: list[str], book: str | None, severity: str | None, rule_code: str | None,
           include_rule: bool = True) -> tuple[str, dict]:
    where = ["1=1"]
    p: dict = {}
    if batch_ids:
        where.append("e.batch_id = any(cast(:ids as uuid[]))")
        p["ids"] = batch_ids
    else:
        where.append("b.is_active")           # default scope: what is currently loaded
    if book:
        where.append("b.book = :book")
        p["book"] = book
    if severity:
        where.append("e.severity = :sev")
        p["sev"] = severity
    if rule_code and include_rule:
        where.append("e.rule_code = :rule")
        p["rule"] = rule_code
    return " and ".join(where), p


def list_errors(engine: Engine, *, batch_ids: list[str], book: str | None, severity: str | None,
                rule_code: str | None, page: int, page_size: int) -> dict:
    where, p = _scope(batch_ids, book, severity, rule_code)
    with engine.connect() as conn:
        total = conn.execute(text(f"select count(*) from ingestion_errors e join ingestion_batches b "
                                  f"on b.id = e.batch_id where {where}"), p).scalar_one()
        items = conn.execute(text(f"""
            select e.id, e.batch_id, b.book, b.source_filename as file, e.source_row, e.transaction_id,
                   e.field, e.value, e.rule_code, e.message, e.severity
              from ingestion_errors e join ingestion_batches b on b.id = e.batch_id
             where {where} order by b.created_at desc, e.source_row nulls first, e.id
             limit :lim offset :off"""), {**p, "lim": page_size, "off": (page - 1) * page_size}).mappings().all()
        w2, p2 = _scope(batch_ids, book, severity, rule_code, include_rule=False)
        codes = conn.execute(text(f"select distinct e.rule_code from ingestion_errors e join ingestion_batches b "
                                  f"on b.id = e.batch_id where {w2} order by 1"), p2).scalars().all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [{**dict(i), "batch_id": str(i["batch_id"])} for i in items], "rule_codes": list(codes)}


CSV_HEADER = ["file", "source_row", "transaction_id", "field", "value", "rule_code", "message", "severity"]


def _csv_safe(v) -> str:
    """Neutralise spreadsheet formula injection (cell starts with = + @ or '-x')."""
    s = "" if v is None else str(v)
    if s and (s[0] in "=+@\t\r" or (s[0] == "-" and len(s) > 1 and not (s[1].isdigit() or s[1] == "."))):
        return "'" + s
    return s


def errors_csv(engine: Engine, batch_ids: list[str]) -> Iterator[str]:
    def line(cells) -> str:
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\r\n").writerow(cells)
        return buf.getvalue()

    yield "﻿" + line(CSV_HEADER)        # BOM so Excel opens it as UTF-8
    with engine.connect() as conn:
        res = conn.execution_options(stream_results=True, yield_per=2000).execute(text("""
            select b.source_filename, e.source_row, e.transaction_id, e.field, e.value, e.rule_code,
                   e.message, e.severity
              from ingestion_errors e join ingestion_batches b on b.id = e.batch_id
             where e.batch_id = any(cast(:ids as uuid[]))
             order by b.created_at, e.source_row nulls first, e.id"""), {"ids": batch_ids})
        for r in res:
            yield line([_csv_safe(c) for c in r])
