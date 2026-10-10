"""AI gap ladder API against the real sample files."""
import csv
import io
from datetime import date

import pytest

from tests.integration.conftest import auth, upload


@pytest.fixture(autouse=True)
def fresh_cache():
    from app.services import behavioral_queries
    behavioral_queries.invalidate()           # tests truncate tables directly, outside the upload endpoint
    yield


@pytest.fixture
def loaded(client):
    for book in ("asset", "liability", "historical"):
        assert upload(client, book).status_code == 201


def get(client, path, headers=None, **params):
    r = client.get(path, params=params, headers=headers or auth())
    assert r.status_code == 200, r.text
    return r


def js(client, path, headers=None, **params):
    return get(client, path, headers, **params).json()


def test_needs_login_snapshot_and_history(client):
    assert client.get("/api/behavioral/ladder").status_code == 401
    r = client.get("/api/behavioral/ladder", headers=auth())
    assert r.status_code == 409 and r.json()["error"]["code"] == "SNAPSHOT_NOT_READY"
    upload(client, "asset")
    upload(client, "liability")
    r = client.get("/api/behavioral/compare", headers=auth())
    assert r.status_code == 409 and r.json()["error"]["code"] == "MODEL_NOT_READY"


def test_ai_ladder_only_moves_dates_never_amounts(client, loaded):
    ai, dl = js(client, "/api/behavioral/ladder"), js(client, "/api/ladder")
    md = ai["metadata"]
    assert (md["transactions_read"], md["transactions_in_ladder"]) == (1856, 1856)
    assert md["model"]["trained_on"] == 14325 and md["model"]["name"] == "early-exit-logit-v2"
    assert 0 < md["moved_count"] <= md["shifted_count"] <= 1856
    for ccy in {r["currency"] for r in dl["ladder"]}:
        a = [r for r in ai["ladder"] if r["currency"] == ccy and r["scope"] == "currency_total"]
        d = [r for r in dl["ladder"] if r["currency"] == ccy and r["scope"] == "currency_total"]
        for k in ("asset_cash_at_maturity", "liability_cash_at_maturity", "net_gap"):
            assert round(sum(r[k] for r in a), 2) == round(sum(r[k] for r in d), 2), (ccy, k)


def test_compare_kpis_months_and_buckets_agree(client, loaded):
    c = js(client, "/api/behavioral/compare", currency="EUR")
    det_rows = {r["bucket"]: r for r in js(client, "/api/ladder")["ladder"]
                if r["currency"] == "EUR" and r["scope"] == "currency_total"}
    assert [b["bucket"] for b in c["buckets"]] == ["0-1M", "1-3M", "3-12M", "1-5Y", ">5Y"]
    for b in c["buckets"]:
        assert b["det_net"] == pytest.approx(det_rows[b["bucket"]]["net_gap"], abs=0.01)
        assert b["p5"] <= b["p50"] <= b["p95"]
    k = c["kpis"]
    assert k["det_total"] == pytest.approx(k["ai_total"], abs=0.01)                 # same total, only timing
    assert 0 < k["moved"] <= k["shifted"] <= k["total"]
    months = c["months"]
    assert len(months) == 25 and months[0]["det_cum"] == 0 and months[0]["ai_cum"] == 0
    for m in months[1:]:
        assert m["diff"] == pytest.approx(m["ai_net"] - m["det_net"], abs=0.01)
    assert k["biggest_gain"]["amount"] == max(m["diff"] for m in months[1:])
    assert k["deepest_opposite"]["amount"] == min(m["diff"] for m in months[1:])
    assert c["recommendations"] and c["overview"]
    assert js(client, "/api/behavioral/compare", currency="EUR")["buckets"] == c["buckets"]   # fixed seed


def test_compare_filters(client, loaded):
    a = js(client, "/api/behavioral/compare", currency="EUR", side="asset")
    assert all(b["det_liabilities"] == 0 and b["p5"] is None for b in a["buckets"])
    s = js(client, "/api/behavioral/compare", currency="EUR", segment="Retail")
    assert s["kpis"]["total"] < js(client, "/api/behavioral/compare", currency="EUR")["kpis"]["total"]
    for params in ({"currency": "XXX"}, {"currency": "EUR", "location": "Atlantis"}, {"segment": "Nope"},
                   {"side": "both"}, {"simulations": 10}):
        assert client.get("/api/behavioral/compare", params=params, headers=auth()).status_code == 422


def test_why_explains_buckets_drivers_movers_and_scenario(client, loaded):
    w = js(client, "/api/behavioral/why", currency="EUR")
    assert [b["bucket"] for b in w["buckets"]] == ["0-1M", "1-3M", "3-12M", "1-5Y", ">5Y"]
    assert sum(b["largest"] for b in w["buckets"]) == 1 and all(b["text"] for b in w["buckets"])
    assert w["drivers"][0]["label"] == "Rate vs market" and w["drivers"][0]["level"] == "High"
    assert w["who_moved"]["total"] == sum(g["count"] for g in w["who_moved"]["groups"])
    assert w["scenario"]["loans_repaid_early"]["change"] == 0
    down = js(client, "/api/behavioral/why", currency="EUR", market_shift_pp=-1.0)["scenario"]
    assert down["loans_repaid_early"]["change"] > 0 > down["deposits_withdrawn_early"]["change"]
    assert client.get("/api/behavioral/why", params={"market_shift_pp": 9}, headers=auth()).status_code == 422


def test_drilldown_filters_kpis_and_reasons(client, loaded):
    page = js(client, "/api/behavioral/transactions", currency="EUR", bucket="1-3M", moved_only=True, page_size=500)
    assert page["total"] > 0 and page["kpis"]["count"] == page["total"]
    assert page["kpis"]["cash_pulled_in"] == pytest.approx(
        sum(t["cash_at_maturity"] * (1 if t["book"] == "asset" else -1) for t in page["items"]), abs=0.01)
    for t in page["items"]:
        assert t["moved"] and t["bucket"] == "1-3M" and t["deterministic_bucket"] != "1-3M"
        assert t["adjusted_maturity_date"] <= t["contractual_maturity_date"]
        assert t["reasons"][0]["text"].startswith("Spread")
    hi = js(client, "/api/behavioral/transactions", currency="EUR", p_min=0.4, page_size=500)["items"]
    assert all(t["p_early"] >= 0.4 for t in hi)
    sp = js(client, "/api/behavioral/transactions", spread_min=1.0, page_size=500)["items"]
    assert sp and all(t["spread_pp"] >= 1.0 for t in sp)
    seg = js(client, "/api/behavioral/transactions", segment="Corporate", type="asset", page_size=500)["items"]
    assert {(t["customer_segment"], t["book"]) for t in seg} == {("Corporate", "asset")}
    assert client.get("/api/behavioral/transactions", params={"sort": "nope"}, headers=auth()).status_code == 422


def test_explain_gives_rationale_and_formula(client, loaded):
    t = js(client, "/api/behavioral/transactions", currency="EUR", moved_only=True, page_size=1)["items"][0]
    e = js(client, f"/api/behavioral/explain/{t['transaction_id']}")
    assert e["adjusted_maturity_date"] == t["adjusted_maturity_date"] and e["ai_bucket"] == t["bucket"]
    assert e["deterministic_bucket"] == t["deterministic_bucket"] and e["formula"].endswith(e["adjusted_maturity_date"])
    assert e["cash_at_maturity"] == t["cash_at_maturity"]
    assert {"incentive_pp", "client_rating", "customer_segment"} <= {d["feature"] for d in e["rationale"]["drivers"]}
    assert client.get("/api/behavioral/explain/AST-999999", headers=auth()).status_code == 404


def test_complete_prediction_exports(client, loaded):
    live = list(csv.DictReader(io.StringIO(get(client, "/api/behavioral/transactions.csv").text)))
    assert len(live) == 1856 and {"p_early", "adjusted_maturity_date", "reasons"} <= set(live[0])
    hist = list(csv.DictReader(io.StringIO(get(client, "/api/behavioral/history-predictions.csv").text)))
    assert len(hist) == 16179
    assert sum(1 for h in hist if h["status"] == "open") == 1854
    assert all(0 < float(h["p_early"]) < 1 for h in hist)


def test_standard_user_only_sees_own_location(client, loaded):
    sg = auth("standard", "Singapore")
    ai = js(client, "/api/behavioral/ladder", sg)
    assert ai["metadata"]["scope_location"] == "Singapore"
    assert {r["location"] for r in ai["ladder"]} <= {"Singapore", None}
    items = js(client, "/api/behavioral/transactions", sg, page_size=500)["items"]
    assert {t["location"] for t in items} == {"Singapore"}
    paris = js(client, "/api/behavioral/transactions", location="Paris", page_size=1)["items"][0]["transaction_id"]
    assert client.get(f"/api/behavioral/explain/{paris}", headers=sg).status_code == 404
    assert js(client, "/api/behavioral/compare", sg)["metadata"]["locations"] == ["Singapore"]
    rows = list(csv.DictReader(io.StringIO(get(client, "/api/behavioral/transactions.csv", sg).text)))
    assert {r["location"] for r in rows} == {"Singapore"}
    hist = list(csv.DictReader(io.StringIO(get(client, "/api/behavioral/history-predictions.csv", sg).text)))
    assert {h["location"] for h in hist} == {"Singapore"}
    assert client.get("/api/behavioral/ladder", headers=auth("standard", None)).status_code == 403


def test_model_card(client, loaded):
    m = js(client, "/api/behavioral/model")
    assert m["trained_on"] == 14325 and m["metrics"]["open_rows"] == 1854 and m["iterations"] < 50
    assert m["drivers"][0]["feature"] == "incentive_pp" and m["drivers"][0]["coefficient"] > 0
    for k in ("auc", "brier", "at_50", "best_f1", "calibration"):
        assert k in m["metrics"]
    assert sum(h["count"] for h in m["exit_timing"]["histogram"]) == m["early_exits"]


def test_calc_date_moves_the_window(client, loaded):
    later = js(client, "/api/behavioral/ladder", calc_date="2027-01-31")
    assert later["metadata"]["calculation_date"] == "2027-01-31" and later["metadata"]["past_due_count"] > 0
    assert date.fromisoformat(later["buckets"][0]["start_date"]) == date(2027, 1, 31)


def test_snapshot_is_cached_and_an_upload_refreshes_it(client, loaded):
    from app.services import behavioral_queries as bq
    first = js(client, "/api/behavioral/ladder")["metadata"]["asset_batch"]["id"]
    n = len(bq.SNAPSHOTS.data)
    js(client, "/api/behavioral/compare", currency="EUR")
    js(client, "/api/behavioral/why", currency="EUR")
    assert len(bq.SNAPSHOTS.data) == n                      # same book: read once, reused by every view
    assert upload(client, "asset").status_code == 201        # re-upload -> new active batch
    second = js(client, "/api/behavioral/ladder")["metadata"]["asset_batch"]["id"]
    assert second != first
