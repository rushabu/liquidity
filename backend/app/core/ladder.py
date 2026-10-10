"""Deterministic maturity-gap ladder: pure functions, no database, no HTTP.

Rules (identical to the Liquid+ deterministic engine specification):
* Interest was already computed at ingest by `liquidity_core` (simple interest, ACT/365, half-up, 2 dp per
  transaction). Here a transaction's cash flow is `cash_at_maturity = principal + interest`.
* Buckets are measured from the calculation date in CALENDAR months: 0-1M, 1-3M, 3-12M, 1-5Y, >5Y.
  The upper edge is inclusive (maturing exactly on calc + 1 month is 0-1M). Month-end dates clamp
  (31 Jan + 1 month = 28/29 Feb). Due today is 0-1M.
* Maturity before the calculation date is PAST DUE: excluded from the ladder and reported, unless the
  policy is `bucket_0_1m`.
* Net gap = assets - liabilities, per bucket, per currency, per location, plus a per-currency roll-up
  (location = None). Currencies are never added together (no FX rate in the use case).
* `maturity_resolver` is the hook for later AI-adjusted / what-if modes: interest stays on the contractual
  dates, only the bucketing date changes.
"""

from __future__ import annotations

import calendar
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .liquidity_core import DAY_COUNT_BASIS

BUCKET_KEYS = ("0-1M", "1-3M", "3-12M", "1-5Y", ">5Y")
BUCKET_LABELS = {"0-1M": "0 – 1 month", "1-3M": "1 – 3 months", "3-12M": "3 – 12 months",
                 "1-5Y": "1 – 5 years", ">5Y": "Over 5 years"}
BUCKET_MONTHS = (1, 3, 12, 60)           # upper edges, in calendar months from the calculation date
PAST_DUE_POLICIES = ("exclude", "bucket_0_1m")
CENT = Decimal("0.01")

Row = dict                               # one stored transaction (see ladder_queries.fetch_snapshot)
MaturityResolver = Callable[[Row], date]


@dataclass(frozen=True)
class Bucket:
    key: str
    label: str
    start: date
    end: date | None                     # None = open ended (>5Y)


def add_months(d: date, months: int) -> date:
    """Calendar-month addition with month-end clamping (same as Excel EDATE)."""
    idx = d.year * 12 + (d.month - 1) + months
    year, month = divmod(idx, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def build_buckets(calc_date: date) -> list[Bucket]:
    uppers = [add_months(calc_date, m) for m in BUCKET_MONTHS]
    out: list[Bucket] = []
    start = calc_date
    for key, upper in zip(BUCKET_KEYS[:-1], uppers, strict=True):
        out.append(Bucket(key, BUCKET_LABELS[key], start, upper))
        start = upper + timedelta(days=1)
    out.append(Bucket(BUCKET_KEYS[-1], BUCKET_LABELS[BUCKET_KEYS[-1]], start, None))
    return out


def assign_bucket(maturity: date, buckets: list[Bucket]) -> str | None:
    """Bucket key, or None when the maturity is before the calculation date (past due)."""
    if maturity < buckets[0].start:
        return None
    for b in buckets:
        if b.end is None or maturity <= b.end:
            return b.key
    raise AssertionError("unreachable: last bucket is open ended")


def contractual_maturity(row: Row) -> date:
    return row["maturity_date"]


def classify(rows: Iterable[Row], calc_date: date, policy: str = "exclude",
             resolver: MaturityResolver = contractual_maturity) -> tuple[list[dict], list[dict]]:
    """-> (ladder_items, past_due_items). Each item = the row plus bucket, days_to_maturity, past_due."""
    if policy not in PAST_DUE_POLICIES:
        raise ValueError(f"unknown past_due_policy '{policy}'")
    buckets = build_buckets(calc_date)
    ladder: list[dict] = []
    past_due: list[dict] = []
    for r in rows:
        effective = resolver(r)
        bucket = assign_bucket(effective, buckets)
        is_past = bucket is None
        if is_past and policy == "bucket_0_1m":
            bucket = BUCKET_KEYS[0]
        item = {**r, "effective_maturity_date": effective, "days_to_maturity": (effective - calc_date).days,
                "bucket": bucket, "past_due": is_past}
        if is_past:
            past_due.append(item)
        if bucket is not None:
            ladder.append(item)
    return ladder, past_due


def _gap_status(net: Decimal) -> str:
    return "surplus" if net > 0 else "shortfall" if net < 0 else "balanced"


def aggregate(items: Iterable[dict], buckets: list[Bucket]) -> list[dict]:
    """Five bucket rows for every currency x location, plus a roll-up (location None) per currency."""
    z = Decimal(0)
    cells: dict[tuple, dict] = {}
    groups: set[tuple[str, str | None]] = set()
    for it in items:
        for loc in (it["location"], None):
            groups.add((it["currency"], loc))
            c = cells.setdefault((it["currency"], loc, it["bucket"]),
                                 {"a_p": z, "a_c": z, "l_p": z, "l_c": z, "a_n": 0, "l_n": 0})
            k = "a" if it["book"] == "asset" else "l"
            c[f"{k}_p"] += it["amount"]
            c[f"{k}_c"] += it["cash_at_maturity"]
            c[f"{k}_n"] += 1
    rows: list[dict] = []
    for currency, location in sorted(groups, key=lambda g: (g[0], g[1] or "")):
        for b in buckets:
            c = cells.get((currency, location, b.key)) or {"a_p": z, "a_c": z, "l_p": z, "l_c": z, "a_n": 0, "l_n": 0}
            net = c["a_c"] - c["l_c"]
            rows.append({
                "currency": currency, "location": location,
                "scope": "currency_total" if location is None else "location",
                "bucket": b.key, "bucket_label": b.label,
                "start_date": b.start, "end_date": b.end,
                "asset_principal": c["a_p"], "asset_cash_at_maturity": c["a_c"],
                "liability_principal": c["l_p"], "liability_cash_at_maturity": c["l_c"],
                "net_gap": net, "gap_status": _gap_status(net),
                "asset_count": c["a_n"], "liability_count": c["l_n"],
            })
    return rows


def simple_interest_with_basis(amount: Decimal, rate_pct: Decimal, days: int, basis: int) -> Decimal:
    return (amount * rate_pct / Decimal(100) * Decimal(days) / Decimal(basis)).quantize(CENT, ROUND_HALF_UP)


def explain_transaction(row: Row, calc_date: date, policy: str = "exclude",
                        resolver: MaturityResolver = contractual_maturity) -> dict:
    """Six readable steps showing exactly how one transaction reaches its bucket."""
    buckets = build_buckets(calc_date)
    effective = resolver(row)
    bucket = assign_bucket(effective, buckets)
    past_due = bucket is None
    final = BUCKET_KEYS[0] if past_due and policy == "bucket_0_1m" else bucket
    sign = "+" if row["book"] == "asset" else "−"
    days = (row["maturity_date"] - row["transaction_date"]).days
    recomputed = simple_interest_with_basis(row["amount"], row["interest_rate_pct"], days, DAY_COUNT_BASIS)
    stored_ok = recomputed == row["interest_amount"]
    steps = [
        {"n": 1, "title": "Read the stored transaction",
         "working": f"{row['book']} {row['transaction_id']} · {row['currency']} · {row['location']}",
         "result": f"principal {row['amount']:,.2f} at {row['interest_rate_pct']}% "
                   f"from {row['transaction_date']} to {row['maturity_date']}"},
        {"n": 2, "title": "Tenor in days (actual calendar days)",
         "working": f"{row['maturity_date']} − {row['transaction_date']}", "result": f"{days} days"},
        {"n": 3, "title": "Simple interest, Actual/365, rounded half-up to 2 dp",
         "working": f"{row['amount']:,.2f} × {row['interest_rate_pct']}% × {days} / {DAY_COUNT_BASIS}",
         "result": f"{recomputed:,.2f}" + ("" if stored_ok else f" (stored value differs: {row['interest_amount']:,.2f})")},
        {"n": 4, "title": "Cash at maturity = principal + interest",
         "working": f"{row['amount']:,.2f} + {row['interest_amount']:,.2f}",
         "result": f"{row['cash_at_maturity']:,.2f}"},
        {"n": 5, "title": "Bucket from the calculation date (upper edge inclusive)",
         "working": f"calc date {calc_date}; maturity {effective}; {(effective - calc_date).days} days away; edges "
                    + ", ".join(f"{b.key}≤{b.end}" for b in buckets[:-1]),
         "result": ("PAST DUE (before calc date)" + (" → counted in 0-1M by policy" if final else " → excluded"))
         if past_due else f"{bucket}"},
        {"n": 6, "title": "Effect on the net gap (assets − liabilities)",
         "working": f"{sign} {row['cash_at_maturity']:,.2f} {row['currency']}",
         "result": ("adds to assets" if row["book"] == "asset" else "adds to liabilities")
         + (f" in {final} for {row['currency']} / {row['location']}" if final else ": not in the ladder")},
    ]
    return {"transaction_id": row["transaction_id"], "type": row["book"], "currency": row["currency"],
            "location": row["location"], "bucket": final, "past_due": past_due,
            "status": "past_due" if past_due and not final else "in_ladder",
            "interest": row["interest_amount"], "cash_at_maturity": row["cash_at_maturity"],
            "stored_interest_matches_recomputation": stored_ok, "steps": steps}


def assumptions(policy: str) -> list[str]:
    return [
        "Interest: simple, Actual/365, no compounding, rounded half-up to 2 dp per transaction (done at ingest).",
        "Cash flow at contractual maturity = principal + interest; the market rate is stored but not used.",
        "Buckets: calendar months from the calculation date; upper edge inclusive; month-end dates clamp.",
        "Due today is in 0-1M. " + ("Past-due contracts are counted in 0-1M and flagged."
                                    if policy == "bucket_0_1m" else "Past-due contracts are excluded and listed."),
        "Net gap = assets − liabilities. Currencies are never combined (no FX rate provided).",
        "Only rows that passed ingest validation are used; rejected rows are in the ingestion error report.",
        "Maturity is the contractual date (no behavioural adjustment in this deterministic ladder).",
    ]
