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


_ERROR_FROM = "from ingestion_errors e join ingestion_batches b on b.id = e.batch_id"
_ERROR_ORDER = "order by b.created_at desc, e.source_row nulls first, e.id"     # the on-screen order


def list_errors(engine: Engine, *, batch_ids: list[str], book: str | None, severity: str | None,
                rule_code: str | None, page: int, page_size: int) -> dict:
    where, p = _scope(batch_ids, book, severity, rule_code)
    with engine.connect() as conn:
        total = conn.execute(text(f"select count(*) {_ERROR_FROM} where {where}"), p).scalar_one()
        items = conn.execute(text(f"""
            select e.id, e.batch_id, b.book, b.source_filename as file, e.source_row, e.transaction_id,
                   e.field, e.value, e.rule_code, e.message, e.severity
              {_ERROR_FROM}
             where {where} {_ERROR_ORDER}
             limit :lim offset :off"""), {**p, "lim": page_size, "off": (page - 1) * page_size}).mappings().all()
        w2, p2 = _scope(batch_ids, book, severity, rule_code, include_rule=False)
        codes = conn.execute(text(f"select distinct e.rule_code {_ERROR_FROM} where {w2} order by 1"),
                             p2).scalars().all()
    return {"total": total, "page": page, "page_size": page_size,
            "items": [{**dict(i), "batch_id": str(i["batch_id"])} for i in items], "rule_codes": list(codes)}


# Same columns, order and wording as the Error report table on the Data ingestion page. The table shows the
# rule code with its message underneath and the severity as the chip colour; the CSV gives those their own columns.
CSV_HEADER = ["File", "Row", "Transaction", "Field", "Value found", "Rule broken", "Message", "Severity"]
EMPTY = "\u2014"                       # the "—" the table shows for a missing value
SEVERITY_LABEL = {"error": "Error (row rejected)", "warning": "Warning"}


def _csv_safe(v) -> str:
    """Neutralise spreadsheet formula injection (cell starts with = + @ or '-x')."""
    s = "" if v is None else str(v)
    if s and (s[0] in "=+@\t\r" or (s[0] == "-" and len(s) > 1 and not (s[1].isdigit() or s[1] == "."))):
        return "'" + s
    return s


def errors_csv(engine: Engine, *, batch_ids: list[str], book: str | None = None, severity: str | None = None,
               rule_code: str | None = None) -> Iterator[str]:
    """The Error report exactly as the table shows it: same scope (the given batches, else everything active),
    same filters, same order and the same cell text, with every row instead of one page."""
    def line(cells) -> str:
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\r\n").writerow(cells)
        return buf.getvalue()

    def cell(v) -> str:
        return EMPTY if v is None or v == "" else _csv_safe(v)

    where, p = _scope(batch_ids, book, severity, rule_code)
    yield "\ufeff" + line(CSV_HEADER)        # BOM so Excel opens it as UTF-8
    with engine.connect() as conn:
        res = conn.execution_options(stream_results=True, yield_per=2000).execute(text(f"""
            select b.source_filename, e.source_row, e.transaction_id, e.field, e.value, e.rule_code,
                   e.message, e.severity
              {_ERROR_FROM}
             where {where} {_ERROR_ORDER}"""), p)
        for f, row, tid, field, value, rule, msg, sev in res:
            yield line([cell(f), cell(row), cell(tid), cell(field), cell(value), cell(rule), cell(msg),
                        SEVERITY_LABEL.get(sev, cell(sev))])
