"""Deterministic gap ladder API. Read-only; computed on request from the active snapshot.

Same snapshot + same parameters = same numbers (no randomness, no caching, no hidden state).
A standard user only ever receives rows from their own location (filtered in SQL).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query

from ..core import ladder as core
from ..db import get_engine
from ..deps import CurrentUser, get_current_user
from ..services.errors import ApiError
from ..services.ladder_queries import fetch_snapshot

router = APIRouter(prefix="/api/ladder", tags=["ladder"])

Policy = Literal["exclude", "bucket_0_1m"]
SORTS = {"transaction_id", "maturity_date", "amount", "interest_amount", "cash_at_maturity"}
CURRENCIES = {"USD", "EUR", "GBP", "SGD", "HKD"}


def _f(v):
    """Decimal -> float, date -> iso string (amounts have 2 dp, well inside float precision)."""
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, date):
        return v.isoformat()
    return v


def _plain(d: dict) -> dict:
    return {k: _f(v) for k, v in d.items()}


def _context(user: CurrentUser, as_of_date: date | None, calc_date: date | None, policy: str):
    as_of, batches, rows = fetch_snapshot(get_engine(), user, as_of_date)
    calc = calc_date or as_of
    ladder_items, past_due = core.classify(rows, calc, policy)
    return as_of, calc, batches, rows, ladder_items, past_due


@router.get("")
def get_ladder(
    as_of_date: date | None = Query(None, description="snapshot date; default newest date with both books active"),
    calc_date: date | None = Query(None, description="bucket anchor; default = the snapshot date"),
    past_due_policy: Policy = "exclude",
    user: CurrentUser = Depends(get_current_user),
):
    as_of, calc, batches, rows, items, past_due = _context(user, as_of_date, calc_date, past_due_policy)
    buckets = core.build_buckets(calc)
    warnings = []
    if past_due:
        warnings.append(f"{len(past_due)} transaction(s) mature before the calculation date "
                        + ("and are counted in 0-1M." if past_due_policy == "bucket_0_1m"
                           else "and are excluded from the ladder."))
    if calc != as_of:
        warnings.append(f"Calculation date {calc} differs from the snapshot date {as_of}.")
    return {
        "metadata": {
            "as_of_date": as_of.isoformat(), "calculation_date": calc.isoformat(),
            "past_due_policy": past_due_policy, "day_count_basis": 365,
            "scope_location": None if user.is_support else user.location,
            "asset_batch": {"id": batches["asset"]["id"], "filename": batches["asset"]["source_filename"],
                            "rows": batches["asset"]["rows_valid"]},
            "liability_batch": {"id": batches["liability"]["id"], "filename": batches["liability"]["source_filename"],
                                "rows": batches["liability"]["rows_valid"]},
            "transactions_read": len(rows), "transactions_in_ladder": len(items),
            "past_due_count": len(past_due),
        },
        "buckets": [{"key": b.key, "label": b.label, "start_date": b.start.isoformat(),
                     "end_date": b.end.isoformat() if b.end else None} for b in buckets],
        "ladder": [_plain(r) for r in core.aggregate(items, buckets)],
        "warnings": warnings,
        "assumptions": core.assumptions(past_due_policy),
    }


@router.get("/transactions")
def get_transactions(
    as_of_date: date | None = None,
    calc_date: date | None = None,
    past_due_policy: Policy = "exclude",
    currency: str | None = None,
    location: str | None = None,
    bucket: str | None = Query(None, description="0-1M, 1-3M, 3-12M, 1-5Y, >5Y or past_due"),
    type: Literal["asset", "liability"] | None = None,
    sort: str = "maturity_date",
    order: Literal["asc", "desc"] = "asc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    """Drill-down: the exact transactions behind any ladder cell."""
    if currency and currency not in CURRENCIES:
        raise ApiError(422, "INVALID_FILTER", f"currency must be one of {sorted(CURRENCIES)}")
    if bucket and bucket != "past_due" and bucket not in core.BUCKET_KEYS:
        raise ApiError(422, "INVALID_FILTER", f"bucket must be one of {list(core.BUCKET_KEYS) + ['past_due']}")
    if sort not in SORTS:
        raise ApiError(422, "INVALID_SORT", f"sort must be one of {sorted(SORTS)}")
    _, calc, _, _, items, past_due = _context(user, as_of_date, calc_date, past_due_policy)
    pool = past_due if bucket == "past_due" else items
    sel = [t for t in pool
           if (not currency or t["currency"] == currency) and (not location or t["location"] == location)
           and (not type or t["book"] == type) and (bucket in (None, "past_due") or t["bucket"] == bucket)]
    sel.sort(key=lambda t: (t[sort], t["transaction_id"]), reverse=(order == "desc"))
    summary = None
    if currency:
        a = sum((t["cash_at_maturity"] for t in sel if t["book"] == "asset"), Decimal(0))
        l = sum((t["cash_at_maturity"] for t in sel if t["book"] == "liability"), Decimal(0))
        summary = {"assets": float(a), "liabilities": float(l), "net_gap": float(a - l), "currency": currency}
    start = (page - 1) * page_size
    keep = ("transaction_id", "book", "entity", "location", "customer_id", "currency", "amount", "interest_rate_pct",
            "transaction_date", "maturity_date", "tenor_days", "interest_amount", "cash_at_maturity", "bucket",
            "days_to_maturity", "past_due")
    return {"total": len(sel), "page": page, "page_size": page_size, "calculation_date": calc.isoformat(),
            "summary": summary, "items": [_plain({k: t[k] for k in keep}) for t in sel[start:start + page_size]]}


@router.get("/explain/{transaction_id}")
def explain(
    transaction_id: str,
    as_of_date: date | None = None,
    calc_date: date | None = None,
    past_due_policy: Policy = "exclude",
    user: CurrentUser = Depends(get_current_user),
):
    as_of, calc, _, rows, _, _ = _context(user, as_of_date, calc_date, past_due_policy)
    row = next((r for r in rows if r["transaction_id"] == transaction_id), None)
    if row is None:
        raise ApiError(404, "TRANSACTION_NOT_FOUND",
                       f"{transaction_id} is not in the active snapshot you can see (it may have been rejected "
                       "at ingest, belong to another location, or be in a different snapshot)")
    out = core.explain_transaction(row, calc, past_due_policy)
    out["calculation_date"] = calc.isoformat()
    return _plain(out) | {"steps": out["steps"]}
