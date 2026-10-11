"""What-if scenarios: shift asset and/or liability maturities (e.g. liabilities 1 month earlier, assets 2 weeks
later) and see the effect on the gap ladder, starting from either the contractual (deterministic) ladder or the
AI-adjusted one.

Rules
* A shift moves the maturity used for bucketing: calendar months first (month-end clamps, like the ladder),
  then days. Negative = earlier, positive = later. Cash amounts never change, so the total net gap is the same.
* A maturity shifted before the calculation date is due now: it lands in 0-1M (it does not disappear).
* Contracts that are already past due in the base view keep their base treatment (past-due policy).
* Location scoping is the same as every other ladder: a standard user only sees their own location.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query

from ..core import behavioral as bm
from ..core import ladder as det
from ..db import get_engine
from ..deps import CurrentUser, get_current_user
from ..services import behavioral_queries as bq
from ..services.errors import ApiError
from .ladder import CURRENCIES, Policy

router = APIRouter(prefix="/api/whatif", tags=["what-if"])

Base = Literal["deterministic", "ai"]
MAX_MOVERS = 100


def shift_date(d: date, months: int, days: int) -> date:
    return det.add_months(d, months) + timedelta(days=days)


def describe(months: int, days: int) -> str:
    """'1 month earlier', '2 weeks later', '+1 months -3 days' (mixed signs), 'unchanged'."""
    if not months and not days:
        return "unchanged"
    if months and days and (months < 0) != (days < 0):
        return f"{months:+d} months {days:+d} days"
    parts = []
    if months:
        parts.append(f"{abs(months)} month{'s' if abs(months) != 1 else ''}")
    if days:
        weeks, rest = divmod(abs(days), 7)
        parts.append(f"{weeks} week{'s' if weeks != 1 else ''}" if not rest
                     else f"{abs(days)} day{'s' if abs(days) != 1 else ''}")
    return " ".join(parts) + (" earlier" if (months or days) < 0 else " later")


def apply_shift(items: list[dict], calc: date, shifts: dict[str, tuple[int, int]]) -> list[dict]:
    """Base ladder items -> scenario items (new date and bucket per contract)."""
    buckets = det.build_buckets(calc)
    out = []
    for t in items:
        months, days = shifts[t["book"]]
        base_date = t["effective_maturity_date"]
        new, pulled = base_date, False
        if (months or days) and not t["past_due"]:
            new = shift_date(base_date, months, days)
            if new < calc:
                new, pulled = calc, True
        bucket = t["bucket"] if t["past_due"] else det.assign_bucket(new, buckets)
        out.append({**t, "base_date": base_date, "base_bucket": t["bucket"], "scenario_date": new,
                    "scenario_bucket": bucket, "pulled_to_today": pulled})
    return out


def bucket_table(items: list[dict], calc: date) -> list[dict]:
    z = Decimal(0)
    cell = {(v, k): {"a": z, "l": z} for v in ("base", "scen") for k in det.BUCKET_KEYS}
    moved_in = {k: 0 for k in det.BUCKET_KEYS}
    moved_out = {k: 0 for k in det.BUCKET_KEYS}
    for t in items:
        side = "a" if t["book"] == "asset" else "l"
        cell[("base", t["base_bucket"])][side] += t["cash_at_maturity"]
        cell[("scen", t["scenario_bucket"])][side] += t["cash_at_maturity"]
        if t["base_bucket"] != t["scenario_bucket"]:
            moved_out[t["base_bucket"]] += 1
            moved_in[t["scenario_bucket"]] += 1
    rows, base_cum, scen_cum = [], 0.0, 0.0
    for b in det.build_buckets(calc):
        ba, bl = float(cell[("base", b.key)]["a"]), float(cell[("base", b.key)]["l"])
        sa, sl = float(cell[("scen", b.key)]["a"]), float(cell[("scen", b.key)]["l"])
        base_cum += ba - bl
        scen_cum += sa - sl
        rows.append({"bucket": b.key, "label": b.label, "start_date": b.start.isoformat(),
                     "end_date": b.end.isoformat() if b.end else None,
                     "base_assets": round(ba, 2), "base_liabilities": round(bl, 2), "base_net": round(ba - bl, 2),
                     "scen_assets": round(sa, 2), "scen_liabilities": round(sl, 2), "scen_net": round(sa - sl, 2),
                     "delta": round((sa - sl) - (ba - bl), 2), "base_cum": round(base_cum, 2),
                     "scen_cum": round(scen_cum, 2), "moved_in": moved_in[b.key], "moved_out": moved_out[b.key]})
    return rows


def _base(user: CurrentUser, base: str, as_of_date: date | None, calc_date: date | None,
          policy: str) -> tuple[date, date, list[dict]]:
    if base == "ai":
        from .behavioral import _context                     # needs the historical model
        c = _context(user, as_of_date, calc_date, policy)
        return c["snapshot"], c["calc"], c["ai_items"]
    snapshot, _, rows = bq.fetch_live(get_engine(), user, as_of_date)
    calc = calc_date or snapshot
    items, _ = det.classify(rows, calc, policy)
    return snapshot, calc, items


@router.get("")
def what_if(
    base: Base = Query("deterministic", description="start from the contractual or the AI-adjusted ladder"),
    asset_shift_months: int = Query(0, ge=-24, le=24, description="negative = earlier"),
    asset_shift_days: int = Query(0, ge=-365, le=365, description="negative = earlier (2 weeks = 14)"),
    liability_shift_months: int = Query(0, ge=-24, le=24),
    liability_shift_days: int = Query(0, ge=-365, le=365),
    currency: str | None = Query(None, description="default: first currency in the snapshot"),
    location: str | None = None,
    as_of_date: date | None = None,
    calc_date: date | None = None,
    past_due_policy: Policy = "exclude",
    user: CurrentUser = Depends(get_current_user),
):
    """Base vs scenario net gap per bucket (with cumulative gap), contracts that change bucket, and the
    recommended actions for the scenario."""
    if currency and currency not in CURRENCIES:
        raise ApiError(422, "INVALID_FILTER", f"currency must be one of {sorted(CURRENCIES)}")
    snapshot, calc, items = _base(user, base, as_of_date, calc_date, past_due_policy)
    currencies = sorted({t["currency"] for t in items})
    if not currencies:
        raise ApiError(409, "SNAPSHOT_NOT_READY", "No transactions in the snapshot you can see")
    currency = currency or currencies[0]
    locations = sorted({t["location"] for t in items if t["currency"] == currency})
    if location and location not in locations:
        raise ApiError(422, "INVALID_FILTER", f"location must be one of {locations} for {currency}")

    shifts = {"asset": (asset_shift_months, asset_shift_days),
              "liability": (liability_shift_months, liability_shift_days)}
    scoped = [t for t in items if t["currency"] == currency and (not location or t["location"] == location)]
    scen = apply_shift(scoped, calc, shifts)
    rows = bucket_table(scen, calc)

    movers = [t for t in scen if t["base_bucket"] != t["scenario_bucket"]]
    movers.sort(key=lambda t: (-t["cash_at_maturity"], t["transaction_id"]))
    worst = lambda key: min(rows, key=lambda r: r[key])
    recs = bm.recommend(currency, location, [
        {"bucket": r["bucket"], "start_date": r["start_date"], "det_net": r["base_net"], "ai_net": r["scen_net"],
         "det_assets": r["base_assets"], "ai_assets": r["scen_assets"],
         "det_liabilities": r["base_liabilities"], "ai_liabilities": r["scen_liabilities"]} for r in rows],
        view="Scenario", base="base", cause="Deposits shifted earlier")
    label = f"Assets {describe(*shifts['asset'])} · liabilities {describe(*shifts['liability'])}"
    return {
        "metadata": {"base": base, "as_of_date": snapshot.isoformat(), "calculation_date": calc.isoformat(),
                     "past_due_policy": past_due_policy, "scope_location": None if user.sees_all_locations else user.location,
                     "currency": currency, "location": location, "currencies": currencies, "locations": locations,
                     "shifts": {k: {"months": m, "days": d, "text": describe(m, d)} for k, (m, d) in shifts.items()},
                     "scenario": label},
        "kpis": {
            "total": len(scen), "moved": len(movers),
            "shifted": sum(1 for t in scen if t["scenario_date"] != t["base_date"]),
            "pulled_to_today": sum(1 for t in scen if t["pulled_to_today"]),
            "worst_base": {"bucket": worst("base_net")["bucket"], "net": worst("base_net")["base_net"]},
            "worst_scenario": {"bucket": worst("scen_net")["bucket"], "net": worst("scen_net")["scen_net"]},
            "total_net": rows[-1]["scen_cum"],
        },
        "buckets": rows,
        "movers_total": len(movers),
        "movers": [{"transaction_id": t["transaction_id"], "book": t["book"], "location": t["location"],
                    "customer_segment": t.get("customer_segment"), "cash_at_maturity": float(t["cash_at_maturity"]),
                    "base_date": t["base_date"].isoformat(), "scenario_date": t["scenario_date"].isoformat(),
                    "base_bucket": t["base_bucket"], "scenario_bucket": t["scenario_bucket"],
                    "pulled_to_today": t["pulled_to_today"]} for t in movers[:MAX_MOVERS]],
        "recommendations": recs,
        "notes": [
            f"Base: {'AI-adjusted (behavioural) maturities' if base == 'ai' else 'contractual maturities'}; "
            "the shift is applied on top of it.",
            "Months are calendar months (month-end clamps), then days. Amounts never change, only dates, so the "
            "total net gap is identical in both columns.",
            "A maturity shifted before the calculation date is treated as due now (0-1M).",
        ],
    }
