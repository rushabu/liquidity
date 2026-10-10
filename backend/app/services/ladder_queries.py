"""Read the active snapshot (asset + liability books) for the gap ladder.

Only batches with is_active and status='completed' are read; rejected rows never reach these tables.
Location scoping is applied HERE, in SQL, so a restricted user's balances can never leave the database.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..deps import CurrentUser
from .errors import ApiError

TABLE = {"asset": "assets", "liability": "liabilities"}
AMOUNT_COL = {"asset": "loan_amount", "liability": "amount"}      # the two tables name the principal differently
COLS = ("transaction_id, entity, location, customer_id, {amt} as amount, currency, transaction_date, "
        "maturity_date, interest_rate_pct, tenor_days, interest_amount, cash_at_maturity")


def allowed_location(user: CurrentUser) -> str | None:
    """None = all locations (Support). A standard user sees only their own location."""
    if user.is_support:
        return None
    if not user.location:
        raise ApiError(403, "LOCATION_NOT_ASSIGNED", "Your account has no location, so no balances can be shown")
    return user.location


def active_batches(engine: Engine, as_of: date | None) -> dict[str, dict]:
    """Latest active asset and liability batch (for one date if given, else the newest date having both)."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            select distinct on (book, as_of_date) id, book, as_of_date, source_filename, rows_valid, created_at
              from ingestion_batches
             where is_active and status = 'completed' and book in ('asset', 'liability')
               and (cast(:d as date) is null or as_of_date = :d)
             order by book, as_of_date desc, created_at desc"""), {"d": as_of}).mappings().all()
    by_date: dict[date, dict[str, dict]] = {}
    for r in rows:
        by_date.setdefault(r["as_of_date"], {})[r["book"]] = {**r, "id": str(r["id"])}
    ready = sorted((d for d, b in by_date.items() if len(b) == 2), reverse=True)
    if not ready:
        have = sorted({f"{b}@{d}" for d, bs in by_date.items() for b in bs})
        raise ApiError(409, "SNAPSHOT_NOT_READY",
                       "The ladder needs an active asset book AND liability book for the same date"
                       + (f" ({as_of})" if as_of else ""), {"available": have})
    return by_date[ready[0]]


def fetch_snapshot(engine: Engine, user: CurrentUser, as_of: date | None = None) -> tuple[date, dict, list[dict]]:
    """-> (as_of_date, batches, rows). Rows carry `book`; ordered by transaction_id for determinism."""
    location = allowed_location(user)
    batches = active_batches(engine, as_of)
    snapshot_date = batches["asset"]["as_of_date"]
    rows: list[dict] = []
    with engine.connect() as conn:
        for book, table in TABLE.items():
            res = conn.execute(text(f"""select {COLS.format(amt=AMOUNT_COL[book])} from {table}
                                         where batch_id = :b and (cast(:loc as text) is null or location = :loc)
                                         order by transaction_id"""),
                               {"b": batches[book]["id"], "loc": location}).mappings().all()
            rows.extend({**dict(r), "book": book} for r in res)
    return snapshot_date, batches, rows
