"""Behavioural model on synthetic data with a known answer (no database)."""

import math
import random
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pytest

from app.core import behavioral as bm
from app.core import ladder as det

LOG_START, LOG_END = date(2022, 1, 1), date(2026, 9, 30)


def hist_row(i, *, book="asset", spread=0.0, t0=date(2023, 1, 1), life=720, exit_frac=None, segment="Retail"):
    mat = t0 + timedelta(days=life)
    closure = t0 + timedelta(days=int(exit_frac * life)) if exit_frac is not None else mat
    early = exit_frac is not None
    if mat > LOG_END and not early:                         # still open at the end of the log
        closure, early = None, None
    rate = 5.0 + spread
    return {"transaction_id": f"{'AST' if book == 'asset' else 'LIA'}-{i:06d}", "book": book, "transaction_date": t0,
            "maturity_date": mat, "actual_closure_date": closure, "is_early_exit": early,
            "amount": Decimal("1000000"), "interest_rate_pct": Decimal(str(rate)), "market_rate_pct": Decimal("5.0"),
            "client_rating": 3, "customer_segment": segment, "product": "Loan-Type-A" if book == "asset" else "Product-A",
            "location": "London"}


def synthetic_history(n=1200, seed=7):
    """Early exit probability rises with the incentive; exits happen at 40-90 % of life."""
    rnd = random.Random(seed)
    rows = []
    for i in range(n):
        book = "asset" if i % 2 else "liability"
        incentive = rnd.uniform(-2, 2)
        spread = incentive if book == "asset" else -incentive
        p = 1 / (1 + math.exp(-(1.5 * incentive - 0.5)))
        early = rnd.random() < p
        t0 = date(2022, 6, 1) + timedelta(days=rnd.randint(0, 900))
        rows.append(hist_row(i, book=book, spread=round(spread, 2), t0=t0, life=rnd.randint(200, 500),
                             exit_frac=rnd.uniform(0.4, 0.9) if early else None))
    return rows


@pytest.fixture(scope="module")
def model():
    return bm.train(synthetic_history())


def live_row(tid="AST-900001", *, book="asset", spread=1.5, t0=date(2026, 1, 1), mat=date(2027, 1, 1)):
    amount, rate = Decimal("1000000"), Decimal(str(5.0 + spread))
    interest = det.simple_interest_with_basis(amount, rate, (mat - t0).days, 365)
    return {"transaction_id": tid, "book": book, "entity": "IBC UK", "location": "London", "customer_id": "CUS-00001",
            "amount": amount, "currency": "EUR", "transaction_date": t0, "maturity_date": mat,
            "interest_rate_pct": rate, "market_rate_pct": Decimal("5.0"), "customer_segment": "Retail",
            "client_rating": 3, "product": "Loan-Type-A" if book == "asset" else "Product-A",
            "tenor_days": (mat - t0).days, "interest_amount": interest, "cash_at_maturity": amount + interest}


# ------------------------------------------------------------------------------------------- training
def test_learns_the_incentive_direction_and_reports_metrics(model):
    assert model.coef_num["incentive_pp"] > 0.5                     # bigger incentive -> more early exits
    assert 0.2 < model.base_rate < 0.6
    m = model.metrics
    assert m["auc"] > 0.7 and m["brier"] < m["brier_baseline"]
    assert 0 < m["at_50"]["f1"] <= m["best_f1"]["f1"] <= 1 and 0 < m["at_50"]["accuracy"] <= 1
    assert 0.39 <= model.exit_fractions.min() and model.exit_fractions.max() <= 0.91


def test_from_scratch_fit_solves_the_penalised_problem():
    rnd = np.random.default_rng(3)
    X = rnd.normal(size=(400, 3))
    y = (rnd.random(400) < bm._sigmoid(0.3 + X @ np.array([1.5, -0.7, 0.0]))).astype(float)
    b0, w, it = bm.fit_logistic(X, y, l2=1.0)
    p = bm._sigmoid(b0 + X @ w)
    grad = np.concatenate([[np.sum(p - y)], X.T @ (p - y) + 1.0 * w])
    assert np.max(np.abs(grad)) < 1e-6 and it < 20                  # first-order optimality reached
    assert w[0] > 1.0 and w[1] < -0.3 and abs(w[2]) < 0.3


def test_metric_helpers():
    y = np.array([0, 0, 1, 1])
    assert bm.auc(y, np.array([0.1, 0.4, 0.35, 0.8])) == 0.75
    c = bm.classification(y, np.array([0.1, 0.6, 0.7, 0.2]), 0.5)
    assert (c["tp"], c["fp"], c["fn"], c["tn"]) == (1, 1, 1, 1) and c["f1"] == 0.5 and c["accuracy"] == 0.5


def test_every_row_gets_a_prediction(model):
    hist = synthetic_history(60, seed=11) + [hist_row(9001, t0=date(2026, 3, 1), life=400)]   # one still open
    preds = bm.predict_history(model, hist)
    assert len(preds) == len(hist) and {p["status"] for p in preds} == {"open", "closed"}
    assert all(0 < p["p_early"] < 1 for p in preds)


def test_trains_on_every_closed_contract():
    rows = synthetic_history(300) + [hist_row(5000 + i, t0=date(2026, 3, 1), life=400) for i in range(5)]
    m = bm.train(rows)
    assert m.n_train == len(bm.labelled(rows)) == 300                 # open contracts have no outcome yet
    assert m.metrics["open_rows"] == 5 and m.metrics["history_rows"] == 305


def test_refuses_to_train_without_enough_or_varied_data():
    with pytest.raises(ValueError, match="at least"):
        bm.train([hist_row(i, exit_frac=0.5) for i in range(5)])
    with pytest.raises(ValueError, match="no variation"):
        bm.train([hist_row(i, t0=date(2023, 1, 1) + timedelta(days=i), life=300) for i in range(80)])


# ----------------------------------------------------------------------------------------- prediction
def test_higher_incentive_means_higher_probability(model):
    low, high = live_row("AST-1", spread=-1.5), live_row("AST-2", spread=1.5)
    deposit_low_rate = live_row("LIA-3", book="liability", spread=-1.5)        # deposit paid below market
    p = bm.predict_live(model, [low, high, deposit_low_rate], date(2026, 1, 1))
    assert p[0]["p_early"] < p[1]["p_early"]
    assert p[2]["p_early"] > p[0]["p_early"]


def test_adjusted_maturity_formula_and_interest(model):
    row = live_row(spread=1.5, t0=date(2026, 1, 1), mat=date(2027, 1, 1))
    calc = date(2026, 1, 1)
    (p,) = bm.predict_live(model, [row], calc)
    assert calc <= p["expected_exit_date"] < row["maturity_date"]
    span = (row["maturity_date"] - p["expected_exit_date"]).days
    assert p["shift_days"] == round(p["p_early"] * span)
    assert p["adjusted_maturity_date"] == row["maturity_date"] - timedelta(days=p["shift_days"])
    adj = bm.adjusted_rows([row], [p])[0]
    assert adj["cash_at_maturity"] == row["cash_at_maturity"] and adj["interest_amount"] == row["interest_amount"]


def test_probability_is_conditioned_on_life_already_passed(model):
    young = live_row("AST-1", t0=date(2026, 1, 1), mat=date(2027, 1, 1))
    mid = live_row("AST-2", t0=date(2025, 7, 1), mat=date(2026, 7, 1))       # ~70 % of life passed at calc
    late = live_row("AST-3", t0=date(2025, 1, 1), mat=date(2026, 2, 1))      # ~92 %: past every historical exit
    calc = date(2026, 3, 15)
    young["transaction_date"] = calc
    py, pm, pl = bm.predict_live(model, [young, mid, late], calc)
    assert pm["p_early"] < pm["p_early_base"]
    F = float(model.timing_cdf(np.array([pm["elapsed_fraction"]]))[0])
    b = pm["p_early_base"]
    assert pm["p_early"] == pytest.approx(b * (1 - F) / (1 - b * F), abs=1e-3)
    assert py["p_early"] == pytest.approx(py["p_early_base"], abs=1e-3)
    assert pl["p_early"] == 0 and pl["shift_days"] == 0


def test_past_due_and_unshifted_rows_keep_their_contract(model):
    past = live_row("AST-1", t0=date(2025, 1, 1), mat=date(2026, 1, 1))
    (p,) = bm.predict_live(model, [past], date(2026, 6, 1))
    assert p["p_early"] == 0 and p["adjusted_maturity_date"] == past["maturity_date"] and p["shift_days"] == 0


def test_adjusted_rows_feed_the_deterministic_ladder_unchanged_schema(model):
    rows = [live_row(f"AST-{i}", spread=s) for i, s in enumerate((-1.5, 0, 1.5))]
    preds = bm.predict_live(model, rows, date(2026, 1, 1))
    adj = bm.adjusted_rows(rows, preds)
    items, past = det.classify(adj, date(2026, 1, 1))
    assert len(items) == 3 and not past
    assert all(a["maturity_date"] <= a["contractual_maturity_date"] for a in adj)
    assert sum(a["amount"] for a in adj) == sum(r["amount"] for r in rows)       # principal never changes


# ------------------------------------------------------------------------------------------ rationale
def test_rationale_contributions_add_up_and_point_the_right_way(model):
    row = live_row(spread=1.8)
    (p,) = bm.predict_live(model, [row], date(2026, 1, 1))
    r = bm.rationale(model, row, p)
    total = sum(d["log_odds"] for d in r["drivers"])
    assert bm._sigmoid(model.intercept + total) == pytest.approx(p["p_early_base"], abs=1e-3)
    top = r["drivers"][0]
    assert top["feature"] == "incentive_pp" and top["direction"] == "raises" and top["effect_pp"] > 0
    assert top["value"] == "Rate spread +1.8 pp" and "above" in r["summary"]
    assert bm.reasons(row, p)[:2] == [{"text": "Spread +1.8 pp", "tone": "up"}, {"text": "Refinancing likely", "tone": "neutral"}]


def test_market_shift_changes_the_incentive(model):
    row = live_row(spread=0.5)
    (base,) = bm.predict_live(model, [row], date(2026, 1, 1))
    (lower,) = bm.predict_live(model, [row], date(2026, 1, 1), market_shift_pp=-1.0)   # market falls -> refinance
    assert lower["p_early"] > base["p_early"] and lower["features"]["spread_pp"] == pytest.approx(1.5)


def test_driver_importance_ranks_the_incentive_first(model):
    imp = bm.driver_importance(model)
    assert imp[0]["label"] == "Rate vs market" and imp[0]["level"] == "High" and imp[0]["strength"] == 1.0


# ----------------------------------------------------------------------------------------- simulation
def test_simulation_is_reproducible_and_ordered(model):
    calc = date(2026, 1, 1)
    rows = [live_row(f"AST-{i}", spread=1.0) for i in range(20)] + [
        live_row(f"LIA-{i}", book="liability", spread=-1.0) for i in range(20)]
    preds = bm.predict_live(model, rows, calc)
    a = bm.simulate(model, rows, preds, calc, "exclude", 300, seed=42)
    b = bm.simulate(model, rows, preds, calc, "exclude", 300, seed=42)
    assert a == b
    for v in a.values():
        assert v["p5"] <= v["p50"] <= v["p95"] and 0 <= v["p_shortfall"] <= 1
    assert {k[1] for k in a} == {"London", None}


def test_simulation_with_no_early_exits_equals_the_contractual_ladder(model):
    calc = date(2026, 1, 1)
    rows = [live_row("AST-1"), live_row("LIA-1", book="liability", mat=date(2026, 2, 15))]
    preds = [{**p, "p_early": 0.0} for p in bm.predict_live(model, rows, calc)]
    sims = bm.simulate(model, rows, preds, calc, "exclude", 50, seed=1)
    items, _ = det.classify(rows, calc)
    agg = {(r["currency"], r["location"], r["bucket"]): float(r["net_gap"])
           for r in det.aggregate(items, det.build_buckets(calc))}
    for key, v in sims.items():
        assert v["p5"] == pytest.approx(agg[key]) and v["p95"] == pytest.approx(agg[key])


# ------------------------------------------------------------------------------------- recommendations
def _cell(bucket, ai, det_net=None, p5=None, p_short=0.0, dl=0.0):
    det_net = ai if det_net is None else det_net
    return {"bucket": bucket, "det_net": det_net, "ai_net": ai, "p5": ai if p5 is None else p5,
            "p_shortfall": p_short, "ai_liabilities": 100 + dl, "det_liabilities": 100.0,
            "ai_assets": 50.0, "det_assets": 50.0}


def test_recommendations_roll_surplus_forward_then_fund_the_rest():
    recs = bm.recommend("EUR", None, [
        {**_cell("1-3M", -5_000_000), "start_date": "2026-11-10"},
        _cell("0-1M", 3_000_000),
        {**_cell("3-12M", -4_000_000), "start_date": "2027-01-10"},
        _cell("1-5Y", 0.0),
    ])
    by = {(r["kind"], r["bucket"]): r for r in recs}
    assert set(by) == {("cover", "1-3M"), ("fund", "1-3M"), ("fund", "3-12M")}
    assert by[("cover", "1-3M")]["action"] == "Roll EUR 3.0M of 0-1M surplus into 1M placements – covers 1-3M gap"
    assert by[("fund", "1-3M")]["action"] == "Raise 3M funding – EUR 2.0M via CDs / CP by 10 Nov 2026"
    assert by[("fund", "1-3M")]["severity"] == "high" and by[("fund", "3-12M")]["severity"] == "medium"
    assert by[("fund", "3-12M")]["amount"] == 4_000_000                     # surplus already used up by 1-3M
    assert [r["kind"] for r in recs] == ["fund", "fund", "cover"]            # most urgent first


def test_recommendations_bad_case_retention_and_surplus():
    recs = bm.recommend("EUR", "Paris", [
        _cell("0-1M", -5_200_000, det_net=1_000_000, p5=-7_900_000, dl=6_200_000),
        _cell("3-12M", 3_000_000),
        _cell(">5Y", 2_000_000, p5=-500_000, p_short=0.2),
    ])
    by = {r["kind"]: r for r in recs}
    assert by["fund"]["action"] == "Raise overnight funding – EUR 5.2M via repo / interbank now"
    assert by["buffer"]["action"] == "Arrange standby line – EUR 2.7M for 0-1M bad case"
    assert by["retain"]["action"] == "Offer retention to early-withdrawal deposits – EUR 6.2M in 0-1M"
    assert by["invest"]["action"] == "Invest surplus – EUR 3.0M in 6M T-bills" and by["invest"]["severity"] == "low"
    assert by["keep_liquid"]["action"] == "Invest >5Y surplus short – EUR 2.0M in 1–5Y govt bonds (20% shortfall risk)"
    assert all(r["scope"] == "EUR · Paris" and "\n" not in r["action"] for r in recs)
    assert recs[0]["kind"] == "fund"
    assert bm.recommend("EUR", None, [_cell(k, 0.0) for k in ("0-1M", "1-3M")]) == []
