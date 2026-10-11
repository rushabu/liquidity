"""AI gap ladder API: behaviour-adjusted ladder, comparison with the deterministic ladder, "why it changed",
drill-down with prediction rationale, market-rate scenario, recommendations and full prediction exports.

Same snapshot, buckets, past-due policy and location scoping as /api/ladder. Each live transaction keeps its
cash amount and moves to its behaviourally adjusted maturity (app/core/behavioral.py). Results are cached per
(batches, calculation date, policy, scope, market shift), so repeat views do not recompute.
"""

from __future__ import annotations

import csv
import io
import logging
import threading
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from typing import Literal

import numpy as np
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from ..core import behavioral as bm
from ..core import ladder as det
from ..db import get_engine
from ..deps import CurrentUser, get_current_user
from ..services import behavioral_queries as bq
from ..services.errors import ApiError
from .ladder import CURRENCIES, Policy, _plain

router = APIRouter(prefix="/api/behavioral", tags=["behavioral"])
log = logging.getLogger("liquidplus")

SORTS = {"transaction_id", "maturity_date", "contractual_maturity_date", "amount", "cash_at_maturity",
         "p_early", "shift_days", "spread_pp"}
SEGMENTS = {"Retail", "Corporate", "SME", "Institutional"}
HORIZON_MONTHS = 24
Side = Literal["asset", "liability"]


# ----------------------------------------------------------------------------------------------- context
def _context(user: CurrentUser, as_of_date: date | None, calc_date: date | None, policy: str,
             market_shift_pp: float = 0.0) -> dict:
    engine = get_engine()
    snapshot, batches, rows = bq.fetch_live(engine, user, as_of_date)
    hb, model = bq.get_model(engine, snapshot)
    calc = calc_date or snapshot
    scope = None if user.sees_all_locations else user.location
    shift = round(market_shift_pp, 2)
    key = (batches["asset"]["id"], batches["liability"]["id"], hb["id"], calc, policy, scope, shift)

    def build() -> dict:
        preds = bm.predict_live(model, rows, calc, shift)
        adj = bm.adjusted_rows(rows, preds)
        ai_items, ai_past = det.classify(adj, calc, policy)
        det_items, _ = det.classify(rows, calc, policy)
        det_bucket = {t["transaction_id"]: t["bucket"] for t in det_items}
        by_pred = {p["transaction_id"]: p for p in preds}
        for t in ai_items:
            t["det_bucket"] = det_bucket.get(t["transaction_id"])
            t["moved"] = t["det_bucket"] != t["bucket"]
            t["segment"] = t["customer_segment"]
            t["signed_cash"] = t["cash_at_maturity"] if t["book"] == "asset" else -t["cash_at_maturity"]
        return {"preds": by_pred, "pred_list": preds, "ai_items": ai_items, "ai_past": ai_past,
                "det_items": det_items, "sims": {}}
    ctx = bq.RESULTS.get_or(key, build)
    return {"snapshot": snapshot, "calc": calc, "batches": batches, "hist": hb, "model": model, "rows": rows,
            "scope": scope, "key": key, "shift": shift, **ctx}


def _check(currency: str | None = None, bucket: str | None = None, segment: str | None = None):
    if currency and currency not in CURRENCIES:
        raise ApiError(422, "INVALID_FILTER", f"currency must be one of {sorted(CURRENCIES)}")
    if bucket and bucket != "past_due" and bucket not in det.BUCKET_KEYS:
        raise ApiError(422, "INVALID_FILTER", f"bucket must be one of {list(det.BUCKET_KEYS) + ['past_due']}")
    if segment and segment not in SEGMENTS:
        raise ApiError(422, "INVALID_FILTER", f"segment must be one of {sorted(SEGMENTS)}")


def _model_summary(c: dict) -> dict:
    m: bm.Model = c["model"]
    return {"name": m.name, "historical_batch": {"id": c["hist"]["id"], "filename": c["hist"]["source_filename"],
                                                 "as_of_date": c["hist"]["as_of_date"].isoformat(),
                                                 "rows": c["hist"]["rows_valid"]},
            "type": "logistic regression (from scratch) + historical exit timing",
            "trained_on": m.n_train, "early_exit_rate": round(m.base_rate, 6),
            "holdout_auc": m.metrics.get("auc"), "holdout_brier": m.metrics.get("brier"),
            "holdout_f1": (m.metrics.get("best_f1") or {}).get("f1"),
            "holdout_accuracy": (m.metrics.get("at_50") or {}).get("accuracy")}


def _simulation(c: dict, n_sims: int) -> dict:
    if n_sims not in c["sims"]:
        seed = bm.simulation_seed(*c["key"], n_sims)
        c["sims"][n_sims] = bm.simulate(c["model"], c["rows"], c["pred_list"], c["calc"], c["key"][4], n_sims, seed)
    return c["sims"][n_sims]


def _in(t: dict, currency=None, location=None, side=None, segment=None) -> bool:
    return ((not currency or t["currency"] == currency) and (not location or t["location"] == location)
            and (not side or t["book"] == side) and (not segment or t["customer_segment"] == segment))


def _metadata(c: dict) -> dict:
    return {"as_of_date": c["snapshot"].isoformat(), "calculation_date": c["calc"].isoformat(),
            "past_due_policy": c["key"][4], "scope_location": c["scope"], "market_shift_pp": c["shift"],
            "currencies": sorted({r["currency"] for r in c["rows"]}),
            "locations": sorted({r["location"] for r in c["rows"]}), "model": _model_summary(c)}


# -------------------------------------------------------------------------------------------- AI ladder
@router.get("/ladder")
def get_ladder(
    as_of_date: date | None = Query(None, description="snapshot date; default newest date with both books active"),
    calc_date: date | None = Query(None, description="bucket anchor; default = the snapshot date"),
    past_due_policy: Policy = "exclude",
    user: CurrentUser = Depends(get_current_user),
):
    """AI-adjusted ladder, same shape as /api/ladder (assets, liabilities, net gap per bucket/currency/location)."""
    c = _context(user, as_of_date, calc_date, past_due_policy)
    buckets = det.build_buckets(c["calc"])
    live = [p for p in c["pred_list"] if p["contractual_maturity_date"] >= c["calc"]]
    shifted = [p for p in live if p["shift_days"] > 0]
    warnings = []
    if c["ai_past"]:
        warnings.append(f"{len(c['ai_past'])} transaction(s) mature before the calculation date "
                        + ("and are counted in 0-1M." if past_due_policy == "bucket_0_1m" else "and are excluded."))
    if c["hist"]["as_of_date"] != c["snapshot"]:
        warnings.append(f"Model trained on the historical log of {c['hist']['as_of_date']} "
                        f"(no historical upload for {c['snapshot']}).")
    md = _metadata(c)
    md.update({
        "asset_batch": {"id": c["batches"]["asset"]["id"], "filename": c["batches"]["asset"]["source_filename"],
                        "rows": c["batches"]["asset"]["rows_valid"]},
        "liability_batch": {"id": c["batches"]["liability"]["id"], "filename": c["batches"]["liability"]["source_filename"],
                            "rows": c["batches"]["liability"]["rows_valid"]},
        "day_count_basis": 365, "transactions_read": len(c["rows"]), "transactions_in_ladder": len(c["ai_items"]),
        "past_due_count": len(c["ai_past"]), "shifted_count": len(shifted),
        "moved_count": sum(1 for t in c["ai_items"] if t["moved"]),
        "average_p_early": round(sum(p["p_early"] for p in live) / len(live), 4) if live else 0.0,
        "average_shift_days": round(sum(p["shift_days"] for p in shifted) / len(shifted), 1) if shifted else 0.0,
    })
    return {"metadata": md,
            "buckets": [{"key": b.key, "label": b.label, "start_date": b.start.isoformat(),
                         "end_date": b.end.isoformat() if b.end else None} for b in buckets],
            "ladder": [_plain(r) for r in det.aggregate(c["ai_items"], buckets)],
            "warnings": warnings, "assumptions": bm.assumptions(c["model"], bm.DEFAULT_SIMULATIONS)}


# --------------------------------------------------------------------------------------------- compare
def _bucket_rows(c: dict, currency: str, location: str | None, side: str | None, segment: str | None,
                 sims: dict | None) -> list[dict]:
    z = Decimal(0)
    tot = {(v, k): {"a": z, "l": z, "n": 0} for v in ("det", "ai") for k in det.BUCKET_KEYS}
    for view, items in (("det", c["det_items"]), ("ai", c["ai_items"])):
        for t in items:
            if _in(t, currency, location, side, segment):
                cell = tot[(view, t["bucket"])]
                cell["a" if t["book"] == "asset" else "l"] += t["cash_at_maturity"]
                cell["n"] += 1
    labels = {b.key: b for b in det.build_buckets(c["calc"])}
    out = []
    for k in det.BUCKET_KEYS:
        d, a = tot[("det", k)], tot[("ai", k)]
        dn, an = float(d["a"] - d["l"]), float(a["a"] - a["l"])
        mc = (sims or {}).get((currency, location, k)) if not side and not segment else None
        out.append({"bucket": k, "label": labels[k].label, "start_date": labels[k].start.isoformat(),
                    "end_date": labels[k].end.isoformat() if labels[k].end else None,
                    "det_assets": float(d["a"]), "det_liabilities": float(d["l"]), "det_net": dn,
                    "ai_assets": float(a["a"]), "ai_liabilities": float(a["l"]), "ai_net": an, "delta": an - dn,
                    "det_count": d["n"], "ai_count": a["n"],
                    "p5": mc["p5"] if mc else None, "p50": mc["p50"] if mc else None,
                    "p95": mc["p95"] if mc else None, "p_shortfall": mc["p_shortfall"] if mc else None})
    return out


def _monthly(c: dict, currency: str, location: str | None, side: str | None, segment: str | None) -> list[dict]:
    calc = c["calc"]
    ends = [det.add_months(calc, m).toordinal() for m in range(1, HORIZON_MONTHS + 1)]
    det_flow, ai_flow = np.zeros(HORIZON_MONTHS), np.zeros(HORIZON_MONTHS)
    movers: list[dict[str, float]] = [defaultdict(float) for _ in range(HORIZON_MONTHS)]
    for view, items, flow in (("det", c["det_items"], det_flow), ("ai", c["ai_items"], ai_flow)):
        for t in items:
            if not _in(t, currency, location, side, segment):
                continue
            o = max(t["maturity_date"].toordinal(), calc.toordinal())
            m = int(np.searchsorted(ends, o, side="left"))
            if m >= HORIZON_MONTHS:
                continue
            v = float(t["cash_at_maturity"]) * (1 if t["book"] == "asset" else -1)
            flow[m] += v
            if view == "ai" and t["shift_days"] > 0:
                movers[m]["asset" if t["book"] == "asset" else "liability"] += abs(v)
    out, cd, ca = [], 0.0, 0.0
    out.append({"month": 0, "end_date": calc.isoformat(), "det_net": 0.0, "ai_net": 0.0, "det_cum": 0.0,
                "ai_cum": 0.0, "diff": 0.0, "note": ""})
    for m in range(HORIZON_MONTHS):
        cd += det_flow[m]
        ca += ai_flow[m]
        diff = ai_flow[m] - det_flow[m]
        mv = movers[m]
        if abs(diff) < 1:
            note = "No behavioural change this month."
        elif diff < 0:
            note = ("Deposits expected to leave earlier." if mv["liability"] >= mv["asset"]
                    else "Loan repayments expected earlier move out of this month.")
        else:
            note = ("Loans expected to be repaid earlier: cash arrives sooner." if mv["asset"] >= mv["liability"]
                    else "Deposit outflows move out of this month.")
        out.append({"month": m + 1, "end_date": date.fromordinal(ends[m]).isoformat(),
                    "det_net": round(float(det_flow[m]), 2), "ai_net": round(float(ai_flow[m]), 2),
                    "det_cum": round(cd, 2), "ai_cum": round(ca, 2), "diff": round(float(diff), 2), "note": note})
    return out


def _month_bucket(calc: date, month: int) -> str:
    end = det.add_months(calc, month)
    return det.assign_bucket(end, det.build_buckets(calc)) or det.BUCKET_KEYS[0]


@router.get("/compare")
def compare(
    currency: str | None = None,
    location: str | None = None,
    side: Side | None = None,
    segment: str | None = None,
    as_of_date: date | None = None,
    calc_date: date | None = None,
    past_due_policy: Policy = "exclude",
    simulations: int = Query(bm.DEFAULT_SIMULATIONS, ge=200, le=5000),
    user: CurrentUser = Depends(get_current_user),
):
    """Deterministic vs AI-adjusted: KPIs, cumulative net gap by month (24 months) with the monthly difference,
    bucket table with the Monte Carlo range, recommended actions and the top actions across currencies."""
    _check(currency, segment=segment)
    c = _context(user, as_of_date, calc_date, past_due_policy)
    md = _metadata(c)
    if not md["currencies"]:
        raise ApiError(409, "SNAPSHOT_NOT_READY", "No transactions in the snapshot you can see")
    currency = currency or md["currencies"][0]
    locations = sorted({r["location"] for r in c["rows"] if r["currency"] == currency})
    if location and location not in locations:
        raise ApiError(422, "INVALID_FILTER", f"location must be one of {locations} for {currency}")
    sims = _simulation(c, simulations)
    rows = _bucket_rows(c, currency, location, side, segment, sims)
    months = _monthly(c, currency, location, side, segment)
    scope_items = [t for t in c["ai_items"] if _in(t, currency, location, side, segment)]
    diffs = [m for m in months[1:]]
    gain = max(diffs, key=lambda m: m["diff"])
    loss = min(diffs, key=lambda m: m["diff"])
    recs = bm.recommend(currency, location, _bucket_rows(c, currency, location, None, None, sims))
    overview = []
    for ccy in md["currencies"]:
        overview.extend(r for r in bm.recommend(ccy, None, _bucket_rows(c, ccy, None, None, None, sims))
                        if r["severity"] != "low")
    overview.sort(key=lambda r: ({"high": 0, "medium": 1}[r["severity"]], -r["amount"]))
    for m in months:
        m["bucket"] = _month_bucket(c["calc"], m["month"]) if m["month"] else None
    return {
        "metadata": {**md, "simulations": simulations, "currency": currency, "location": location, "side": side,
                     "segment": segment, "locations": locations, "horizon_months": HORIZON_MONTHS},
        "kpis": {
            "moved": sum(1 for t in scope_items if t["moved"]), "total": len(scope_items),
            "shifted": sum(1 for t in scope_items if t["shift_days"] > 0),
            "biggest_gain": {"month": gain["month"], "amount": gain["diff"]},
            "deepest_opposite": {"month": loss["month"], "amount": loss["diff"]},
            "det_total": sum(r["det_net"] for r in rows), "ai_total": sum(r["ai_net"] for r in rows),
        },
        "months": months,
        "buckets": rows,
        "recommendations": recs,
        "overview": overview[:8],
        "notes": ["Lines: cumulative net gap (assets − liabilities) month by month from the calculation date.",
                  "Bars: AI minus deterministic net cash flow in each month. Amounts never change, only dates, "
                  "so the total net gap is the same in both views.",
                  "Range in the table: P5–P95 of 1,000+ simulated outcomes (only without side/segment filters)."],
    }


# ----------------------------------------------------------------------------------------- why changed
def _why_buckets(c: dict, currency: str, location: str | None) -> list[dict]:
    det_net = defaultdict(float)
    for t in c["det_items"]:
        if _in(t, currency, location):
            det_net[t["bucket"]] += float(t["cash_at_maturity"]) * (1 if t["book"] == "asset" else -1)
    ai_net = defaultdict(float)
    moved_in, moved_out = defaultdict(list), defaultdict(list)
    for t in c["ai_items"]:
        if not _in(t, currency, location):
            continue
        ai_net[t["bucket"]] += float(t["signed_cash"])
        if t["moved"]:
            moved_in[t["bucket"]].append(t)
            if t["det_bucket"]:
                moved_out[t["det_bucket"]].append(t)
    shifts = {k: ai_net[k] - det_net[k] for k in det.BUCKET_KEYS}
    largest = max(det.BUCKET_KEYS, key=lambda k: abs(shifts[k]))
    out = []
    for k in det.BUCKET_KEYS:
        ins, outs = moved_in[k], moved_out[k]
        parts: list[str] = []
        if ins:
            groups = Counter()
            for t in ins:
                groups[(t["customer_segment"], t["book"])] += abs(float(t["cash_at_maturity"]))
            (seg, book), _ = groups.most_common(1)[0]
            mates = [t for t in ins if t["customer_segment"] == seg and t["book"] == book]
            inc = np.mean([t["spread_pp"] if book == "asset" else -t["spread_pp"] for t in mates])
            noun = "loans" if book == "asset" else "deposits"
            if book == "asset":
                story = ("paying above market: borrowers are likely to refinance." if inc > 0
                         else "repaid early despite market pricing (segment and rating effect).")
            else:
                story = ("paid below market: depositors are likely to leave early, so outflows arrive sooner."
                         if inc > 0 else "withdrawn early (segment and rating effect), so outflows arrive sooner.")
            fr = [c["preds"][t["transaction_id"]] for t in ins]
            exit_points = [(p["expected_exit_date"] - t["transaction_date"]).days / max(1, (t["contractual_maturity_date"]
                           - t["transaction_date"]).days) for p, t in zip(fr, ins) if p["expected_exit_date"]]
            parts.append(f"{len(ins)} contracts moved in. Mostly {seg} {noun} {story}")
            if exit_points:
                lo, hi = np.percentile(exit_points, [25, 75])
                span = f"~{lo:.0%}" if round(lo * 100) == round(hi * 100) else f"{lo:.0%}–{hi:.0%}"
                parts.append(f"Predicted exits at {span} of the contract's life.")
        if outs:
            parts.append(f"{len(outs)} contracts move to earlier buckets.")
        if not ins and not outs:
            parts.append("No contracts change bucket.")
        gross = sum(abs(float(t["cash_at_maturity"])) for t in c["ai_items"] if _in(t, currency, location) and t["bucket"] == k)
        if abs(shifts[k]) < 0.01 * max(gross, 1) and (ins or outs):
            parts.append("Small net effect.")
        out.append({"bucket": k, "moved_in": len(ins), "moved_out": len(outs), "shift": round(shifts[k], 2),
                    "det_net": round(det_net[k], 2), "ai_net": round(ai_net[k], 2),
                    "text": " ".join(parts), "largest": k == largest and abs(shifts[k]) > 0})
    return out


def _who_moved(c: dict, currency: str, location: str | None) -> dict:
    movers = [t for t in c["ai_items"] if _in(t, currency, location) and t["moved"]]
    counts = Counter((t["customer_segment"], t["book"]) for t in movers)
    groups = [{"label": f"{seg} {'assets' if book == 'asset' else 'deposits'}", "segment": seg, "side": book,
               "count": n, "share": round(n / len(movers), 4)} for (seg, book), n in counts.most_common()]
    if len(groups) > 4:
        rest = groups[3:]
        groups = groups[:3] + [{"label": "Other", "segment": None, "side": None, "count": sum(g["count"] for g in rest),
                                "share": round(sum(g["share"] for g in rest), 4)}]
    return {"total": len(movers), "groups": groups}


def _expected(ctx: dict, book: str, currency: str, location: str | None) -> float:
    """Expected number of early exits (sum of probabilities) among live contracts of one side in scope."""
    by_id = {r["transaction_id"]: r for r in ctx["rows"]}
    return float(sum(p["p_early"] for p in ctx["pred_list"]
                     if p["book"] == book and _in(by_id[p["transaction_id"]], currency, location)))


def _scenario(user, as_of_date, calc_date, policy, currency, location, shift: float, base: dict) -> dict:
    s = _context(user, as_of_date, calc_date, policy, shift)
    rows_b = _bucket_rows(base, currency, location, None, None, None)
    rows_s = _bucket_rows(s, currency, location, None, None, None)
    buckets = [{"bucket": a["bucket"], "base": a["ai_net"], "scenario": b["ai_net"], "change": b["ai_net"] - a["ai_net"]}
               for a, b in zip(rows_b, rows_s)]
    headline = max(buckets, key=lambda x: abs(x["change"]))
    if abs(headline["change"]) < 1:                      # no shift: show the deepest shortfall to watch
        headline = min(buckets, key=lambda x: x["base"])
    loans_b, loans_s = _expected(base, "asset", currency, location), _expected(s, "asset", currency, location)
    dep_b, dep_s = _expected(base, "liability", currency, location), _expected(s, "liability", currency, location)
    return {"market_shift_pp": shift,
            "loans_repaid_early": {"base": round(loans_b, 1), "scenario": round(loans_s, 1), "change": round(loans_s - loans_b)},
            "deposits_withdrawn_early": {"base": round(dep_b, 1), "scenario": round(dep_s, 1), "change": round(dep_s - dep_b)},
            "buckets": buckets, "headline": headline,
            "note": "Moves every market rate by the same amount and re-scores all contracts with the same model."}


@router.get("/why")
def why_changed(
    currency: str | None = None,
    location: str | None = None,
    market_shift_pp: float = Query(0.0, ge=-3.0, le=3.0, description="parallel market-rate shift for the scenario"),
    as_of_date: date | None = None,
    calc_date: date | None = None,
    past_due_policy: Policy = "exclude",
    user: CurrentUser = Depends(get_current_user),
):
    """Why each bucket moved, what drives the model, who moved, and a market-rate scenario."""
    _check(currency)
    c = _context(user, as_of_date, calc_date, past_due_policy)
    md = _metadata(c)
    if not md["currencies"]:
        raise ApiError(409, "SNAPSHOT_NOT_READY", "No transactions in the snapshot you can see")
    currency = currency or md["currencies"][0]
    return {
        "metadata": {**md, "currency": currency, "location": location},
        "buckets": _why_buckets(c, currency, location),
        "drivers": bm.driver_importance(c["model"]),
        "who_moved": _who_moved(c, currency, location),
        "scenario": _scenario(user, as_of_date, calc_date, past_due_policy, currency, location,
                              round(market_shift_pp, 1), c),
    }


# ----------------------------------------------------------------------------------------- drill-down
def _select(c: dict, bucket, currency, location, side, segment, p_min, p_max, spread_min, spread_max, moved_only):
    pool = c["ai_past"] if bucket == "past_due" else c["ai_items"]
    return [t for t in pool
            if _in(t, currency, location, side, segment)
            and (bucket in (None, "past_due") or t["bucket"] == bucket)
            and p_min <= t["p_early"] <= p_max
            and (spread_min is None or t["spread_pp"] >= spread_min)
            and (spread_max is None or t["spread_pp"] <= spread_max)
            and (not moved_only or t.get("moved"))]


def _txn_out(c: dict, t: dict) -> dict:
    p = c["preds"][t["transaction_id"]]
    keep = ("transaction_id", "book", "entity", "location", "customer_id", "customer_segment", "currency", "amount",
            "interest_rate_pct", "market_rate_pct", "transaction_date", "contractual_maturity_date", "maturity_date",
            "shift_days", "p_early", "interest_amount", "cash_at_maturity", "bucket", "days_to_maturity", "past_due")
    d = _plain({k: t.get(k) for k in keep})
    d["adjusted_maturity_date"] = d.pop("maturity_date")
    d["deterministic_bucket"] = t.get("det_bucket")
    d["moved"] = bool(t.get("moved"))
    d["spread_pp"] = round(t["spread_pp"], 4)
    d["p_early_lifetime"] = p["p_early_base"]
    d["expected_exit_date"] = p["expected_exit_date"].isoformat() if p["expected_exit_date"] else None
    d["reasons"] = bm.reasons(t, p)
    return d


@router.get("/transactions")
def get_transactions(
    as_of_date: date | None = None,
    calc_date: date | None = None,
    past_due_policy: Policy = "exclude",
    currency: str | None = None,
    location: str | None = None,
    bucket: str | None = Query(None, description="AI bucket: 0-1M, 1-3M, 3-12M, 1-5Y, >5Y or past_due"),
    type: Side | None = Query(None, description="asset or liability"),
    segment: str | None = None,
    p_min: float = Query(0.0, ge=0, le=1),
    p_max: float = Query(1.0, ge=0, le=1),
    spread_min: float | None = Query(None, description="contract rate − market rate, pp"),
    spread_max: float | None = None,
    moved_only: bool = Query(False, description="only contracts whose bucket changed"),
    shifted_only: bool = Query(False, description="deprecated alias of moved_only"),
    sort: str = "p_early",
    order: Literal["asc", "desc"] = "desc",
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    """Drill-down behind the AI ladder: contractual vs AI bucket, p(early), spread and the main reasons."""
    _check(currency, bucket, segment)
    if sort not in SORTS:
        raise ApiError(422, "INVALID_SORT", f"sort must be one of {sorted(SORTS)}")
    c = _context(user, as_of_date, calc_date, past_due_policy)
    sel = _select(c, bucket, currency, location, type, segment, p_min, p_max, spread_min, spread_max,
                  moved_only or shifted_only)
    sel.sort(key=lambda t: (t[sort], t["transaction_id"]), reverse=(order == "desc"))
    moved_in = [t for t in sel if t.get("moved")]
    by_ccy: dict[str, Decimal] = {}
    for t in moved_in:                          # never add different currencies together (no FX rate)
        by_ccy[t["currency"]] = by_ccy.get(t["currency"], Decimal(0)) + t["signed_cash"]
    currencies = sorted({t["currency"] for t in sel})
    kpis = {"count": len(sel), "currencies": currencies,
            # one figure only when the selection is a single currency; otherwise see the per-currency split
            "cash_pulled_in": float(by_ccy.get(currencies[0], Decimal(0)))
            if bucket and len(currencies) == 1 else None,
            "cash_pulled_in_by_currency": {k: float(v) for k, v in sorted(by_ccy.items())} if bucket else None,
            "avg_p_early": round(float(np.mean([t["p_early"] for t in sel])), 4) if sel else 0.0,
            "avg_spread_pp": round(float(np.mean([t["spread_pp"] for t in sel])), 4) if sel else 0.0}
    summary = None
    if currency:
        a = sum((t["cash_at_maturity"] for t in sel if t["book"] == "asset"), Decimal(0))
        li = sum((t["cash_at_maturity"] for t in sel if t["book"] == "liability"), Decimal(0))
        summary = {"assets": float(a), "liabilities": float(li), "net_gap": float(a - li), "currency": currency}
    start = (page - 1) * page_size
    return {"total": len(sel), "page": page, "page_size": page_size, "calculation_date": c["calc"].isoformat(),
            "kpis": kpis, "summary": summary, "items": [_txn_out(c, t) for t in sel[start:start + page_size]]}


def _csv(rows: list[dict], cols: list[str], filename: str) -> StreamingResponse:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for v in (r.get(k) for k in cols)])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/transactions.csv")
def export_transactions(
    as_of_date: date | None = None,
    calc_date: date | None = None,
    past_due_policy: Policy = "exclude",
    currency: str | None = None,
    location: str | None = None,
    bucket: str | None = None,
    type: Side | None = None,
    segment: str | None = None,
    p_min: float = Query(0.0, ge=0, le=1),
    p_max: float = Query(1.0, ge=0, le=1),
    spread_min: float | None = None,
    spread_max: float | None = None,
    moved_only: bool = False,
    user: CurrentUser = Depends(get_current_user),
):
    """Every prediction for the live contracts in the selection (no filters = the complete book)."""
    _check(currency, bucket, segment)
    c = _context(user, as_of_date, calc_date, past_due_policy)
    sel = _select(c, bucket, currency, location, type, segment, p_min, p_max, spread_min, spread_max, moved_only)
    sel.sort(key=lambda t: t["transaction_id"])
    rows = []
    for t in sel:
        d = _txn_out(c, t)
        d["reasons"] = "; ".join(r["text"] for r in d["reasons"])
        rows.append(d)
    cols = ["transaction_id", "book", "customer_segment", "location", "currency", "amount", "interest_rate_pct",
            "market_rate_pct", "spread_pp", "p_early_lifetime", "p_early", "transaction_date",
            "contractual_maturity_date", "expected_exit_date", "adjusted_maturity_date", "shift_days",
            "deterministic_bucket", "bucket", "moved", "cash_at_maturity", "reasons"]
    return _csv(rows, cols, f"liquidplus_ai_predictions_{c['calc'].isoformat()}.csv")


@router.get("/explain/{transaction_id}")
def explain(
    transaction_id: str,
    as_of_date: date | None = None,
    calc_date: date | None = None,
    past_due_policy: Policy = "exclude",
    user: CurrentUser = Depends(get_current_user),
):
    """Why one transaction moved: probability, drivers (log-odds), contractual vs predicted exit, formula."""
    c = _context(user, as_of_date, calc_date, past_due_policy)
    row = next((r for r in c["rows"] if r["transaction_id"] == transaction_id), None)
    if row is None:
        raise ApiError(404, "TRANSACTION_NOT_FOUND",
                       f"{transaction_id} is not in the active snapshot you can see (it may have been rejected "
                       "at ingest, belong to another location, or be in a different snapshot)")
    p = c["preds"][transaction_id]
    buckets = det.build_buckets(c["calc"])
    f = p["features"]
    return _plain({
        "transaction_id": transaction_id, "type": row["book"], "currency": row["currency"],
        "location": row["location"], "customer_segment": row["customer_segment"], "calculation_date": c["calc"],
        "p_early_base": p["p_early_base"], "p_early": p["p_early"], "elapsed_fraction": p["elapsed_fraction"],
        "transaction_date": row["transaction_date"], "contractual_maturity_date": row["maturity_date"],
        "expected_exit_date": p["expected_exit_date"], "adjusted_maturity_date": p["adjusted_maturity_date"],
        "shift_days": p["shift_days"], "deterministic_bucket": det.assign_bucket(row["maturity_date"], buckets),
        "ai_bucket": det.assign_bucket(p["adjusted_maturity_date"], buckets),
        "cash_at_maturity": row["cash_at_maturity"], "interest_rate_pct": row["interest_rate_pct"],
        "market_rate_pct": row["market_rate_pct"], "spread_pp": round(f["spread_pp"], 4),
        "tenor_years": round(f["tenor_years"], 2),
    }) | {"rationale": bm.rationale(c["model"], row, p), "reasons": bm.reasons(row, p),
          "formula": (f"adjusted = {row['maturity_date']} − {p['p_early']:.1%} × "
                      f"({row['maturity_date']} − {p['expected_exit_date'] or row['maturity_date']}) "
                      f"= {p['adjusted_maturity_date']}")}


# ------------------------------------------------------------------------------------------- model card
@router.get("/model")
def model_card(as_of_date: date | None = None, user: CurrentUser = Depends(get_current_user)):
    """What the model learned: data, holdout accuracy / F1 / AUC / calibration, drivers and exit timing."""
    hb, m = bq.get_model(get_engine(), as_of_date)
    s = m.exit_fractions
    hist, edges = np.histogram(s, bins=10, range=(0.0, 1.0))
    return {
        "name": m.name, "type": "Logistic regression written from scratch (numpy, Newton-Raphson) + empirical exit timing",
        "historical_batch": {"id": hb["id"], "filename": hb["source_filename"], "as_of_date": hb["as_of_date"].isoformat()},
        "trained_on": m.n_train, "early_exits": m.n_early, "early_exit_rate": round(m.base_rate, 6),
        "iterations": m.iterations, "metrics": m.metrics, "importance": bm.driver_importance(m),
        "drivers": sorted(
            [{"feature": n, "label": bm.NUMERIC_LABEL[n], "coefficient": round(v, 4), "per": "1 standard deviation",
              "std": round(m.num_std[n], 4)} for n, v in m.coef_num.items()]
            + [{"feature": f"{c}={lvl}", "label": f"{bm.CATEGORICAL_LABEL[c]}: {lvl}", "coefficient": round(v, 4),
                "per": "vs average mix", "std": None} for c, d in m.coef_cat.items() for lvl, v in d.items()],
            key=lambda d: -abs(d["coefficient"])),
        "exit_timing": {"min": round(float(s.min()), 3), "median": round(float(np.median(s)), 3),
                        "max": round(float(s.max()), 3),
                        "histogram": [{"from": round(float(edges[i]), 1), "to": round(float(edges[i + 1]), 1),
                                       "count": int(hist[i])} for i in range(len(hist))]},
        "assumptions": bm.assumptions(m, bm.DEFAULT_SIMULATIONS),
    }


@router.get("/history-predictions.csv")
def export_history(as_of_date: date | None = None, user: CurrentUser = Depends(get_current_user)):
    """A prediction for every row of the historical log (closed and still-open contracts)."""
    engine = get_engine()
    hb, m = bq.get_model(engine, as_of_date)
    rows = bq.cached_history(engine, hb["id"])
    loc = None if user.sees_all_locations else user.location
    if not user.sees_all_locations and not loc:
        raise ApiError(403, "LOCATION_NOT_ASSIGNED", "Your account has no location, so no data can be shown")
    preds = [p for p in bm.predict_history(m, rows) if loc is None or p["location"] == loc]
    for p in preds:
        for k in ("transaction_date", "contractual_maturity_date", "actual_closure_date"):
            p[k] = p[k].isoformat() if p[k] else ""
    cols = ["transaction_id", "book", "customer_segment", "location", "transaction_date", "contractual_maturity_date",
            "actual_closure_date", "status", "actual_early_exit", "spread_pp", "p_early"]
    return _csv(preds, cols, f"liquidplus_history_predictions_{hb['as_of_date'].isoformat()}.csv")


# ---------------------------------------------------------------------------------------------- warm-up
def warm() -> None:
    """Pre-compute the default AI views (model, predictions, simulation) for the newest snapshot, so the first
    person to open the AI gap ladder does not wait. Runs in the background; missing data is not an error."""
    support = CurrentUser(id="warm-up", role="support")
    try:
        c = _context(support, None, None, "exclude")
        _simulation(c, bm.DEFAULT_SIMULATIONS)
        log.info("AI gap ladder warmed", extra={"snapshot": c["snapshot"].isoformat()})
    except Exception as e:                      # e.g. SNAPSHOT_NOT_READY / MODEL_NOT_READY / database down
        log.info("AI gap ladder warm-up skipped", extra={"reason": getattr(e, "code", e.__class__.__name__)})


def warm_async() -> None:
    threading.Thread(target=warm, name="ai-warm-up", daemon=True).start()
