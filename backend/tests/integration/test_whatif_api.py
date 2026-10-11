"""What-if tenor shifts against the real sample files."""
from datetime import date, timedelta

import pytest

from app.core.ladder import add_months
from tests.integration.conftest import auth, upload


@pytest.fixture(autouse=True)
def fresh_cache():
    from app.services import behavioral_queries
    behavioral_queries.invalidate()
    yield


@pytest.fixture
def books(client):
    for b in ("asset", "liability"):
        assert upload(client, b).status_code == 201


def wi(client, headers=None, **params):
    r = client.get("/api/whatif", params=params, headers=headers or auth())
    assert r.status_code == 200, r.text
    return r.json()


def test_no_shift_is_the_deterministic_ladder(client, books):
    w = wi(client, currency="EUR")
    det = {r["bucket"]: r for r in client.get("/api/ladder", headers=auth()).json()["ladder"]
           if r["currency"] == "EUR" and r["scope"] == "currency_total"}
    assert w["kpis"]["moved"] == 0 and w["kpis"]["shifted"] == 0 and w["movers"] == []
    for b in w["buckets"]:
        assert b["base_net"] == pytest.approx(det[b["bucket"]]["net_gap"], abs=0.01)
        assert b["scen_net"] == b["base_net"] and b["delta"] == 0
    assert w["metadata"]["scenario"] == "Assets unchanged · liabilities unchanged"


def test_liabilities_one_month_earlier(client, books):
    w = wi(client, currency="EUR", liability_shift_months=-1)
    calc = date.fromisoformat(w["metadata"]["calculation_date"])
    assert w["metadata"]["shifts"]["liability"]["text"] == "1 month earlier"
    assert w["kpis"]["moved"] > 0 and w["kpis"]["pulled_to_today"] > 0
    for b in w["buckets"]:
        assert b["scen_assets"] == b["base_assets"]                       # assets untouched
    assert sum(b["scen_liabilities"] for b in w["buckets"]) == pytest.approx(
        sum(b["base_liabilities"] for b in w["buckets"]), abs=0.05)      # amounts never change
    assert w["buckets"][-1]["scen_cum"] == pytest.approx(w["buckets"][-1]["base_cum"], abs=0.05)
    assert w["buckets"][0]["scen_liabilities"] > w["buckets"][0]["base_liabilities"]   # outflows arrive sooner
    for t in w["movers"]:
        assert t["book"] == "liability"
        want = max(calc, add_months(date.fromisoformat(t["base_date"]), -1))
        assert t["scenario_date"] == want.isoformat() and t["pulled_to_today"] == (want == calc)
    assert [m["cash_at_maturity"] for m in w["movers"]] == sorted((m["cash_at_maturity"] for m in w["movers"]), reverse=True)


def test_assets_two_weeks_later_and_both_sides(client, books):
    w = wi(client, currency="USD", asset_shift_days=14)
    assert w["metadata"]["shifts"]["asset"]["text"] == "2 weeks later" and w["kpis"]["pulled_to_today"] == 0
    for b in w["buckets"]:
        assert b["scen_liabilities"] == b["base_liabilities"]
    for t in w["movers"]:
        assert t["book"] == "asset"
        assert date.fromisoformat(t["scenario_date"]) - date.fromisoformat(t["base_date"]) == timedelta(days=14)
    both = wi(client, currency="USD", asset_shift_days=14, liability_shift_months=-1)
    assert {t["book"] for t in both["movers"]} == {"asset", "liability"}
    assert both["metadata"]["scenario"] == "Assets 2 weeks later · liabilities 1 month earlier"
    moved_in = sum(b["moved_in"] for b in both["buckets"])
    assert moved_in == sum(b["moved_out"] for b in both["buckets"]) == both["kpis"]["moved"]


def test_scenario_recommendations_cover_every_shortfall(client, books):
    w = wi(client, currency="EUR", liability_shift_months=-1)
    for b in w["buckets"]:
        covered = sum(r["amount"] for r in w["recommendations"]
                      if r["bucket"] == b["bucket"] and r["kind"] in ("cover", "fund"))
        assert covered == pytest.approx(max(0.0, -b["scen_net"]), abs=0.05)


def test_contractual_only_no_ml_model(client, books):
    """Task xiii: re-computation with adjusted maturities, no ML model. Works without a historical log, never
    trains a model, and an old `base=ai` parameter is ignored (still the contractual ladder)."""
    from app.services import behavioral_queries as bq
    bq.MODELS.clear()
    w = wi(client, currency="EUR", liability_shift_months=-1)
    assert w["metadata"]["base"] == "deterministic" and w["kpis"]["moved"] > 0
    assert len(bq.MODELS.data) == 0                                                 # nothing was trained
    assert wi(client, currency="EUR", liability_shift_months=-1, base="ai")["buckets"] == w["buckets"]
    assert "No ML model" in " ".join(w["notes"])


def test_location_scope_and_validation(client, books):
    assert client.get("/api/whatif").status_code == 401
    sg = wi(client, auth("standard", "Singapore"), liability_shift_months=-1)
    assert sg["metadata"]["scope_location"] == "Singapore"
    assert {t["location"] for t in sg["movers"]} <= {"Singapore"}
    paris = wi(client, currency="EUR", location="Paris", asset_shift_months=2)
    assert paris["metadata"]["location"] == "Paris" and {t["location"] for t in paris["movers"]} <= {"Paris"}
    for params in ({"asset_shift_months": 25}, {"liability_shift_days": -400}, {"currency": "XXX"},
                   {"currency": "EUR", "location": "Atlantis"}):
        assert client.get("/api/whatif", params=params, headers=auth()).status_code == 422, params


def test_advice_follows_the_shift_direction(client, books):
    later = wi(client, currency="EUR", liability_shift_months=4)
    assert later["recommendations"]
    assert not any(r["kind"] == "retain" for r in later["recommendations"])
    assert not any("earlier" in (r["action"] + r["rationale"]).lower() for r in later["recommendations"])
    sooner = wi(client, currency="EUR", liability_shift_months=-1)
    assert any(r["kind"] == "retain" and "shifted earlier" in r["rationale"] for r in sooner["recommendations"])
    mixed = wi(client, currency="EUR", liability_shift_months=1, liability_shift_days=-3)    # net effect: later
    assert not any(r["kind"] == "retain" for r in mixed["recommendations"])
