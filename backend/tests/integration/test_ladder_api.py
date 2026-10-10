"""Ladder against the real files. Golden values come from the independently verified Liquid+ engine
(Fractions re-implementation + Excel EDATE/SUMIFS proof)."""
import json
from decimal import Decimal

import pytest

from tests.integration.conftest import DATA, auth, upload

GOLDEN = json.loads((DATA / "golden_deterministic_ladder.json").read_text())


@pytest.fixture
def loaded(client):
    assert upload(client, "asset").status_code == 201
    assert upload(client, "liability").status_code == 201


def ladder(client, headers=None, **params):
    r = client.get("/api/ladder", params=params, headers=headers or auth())
    assert r.status_code == 200, r.text
    return r.json()


def test_not_ready_without_both_books(client):
    r = client.get("/api/ladder", headers=auth())
    assert r.status_code == 409 and r.json()["error"]["code"] == "SNAPSHOT_NOT_READY"
    upload(client, "asset")
    assert client.get("/api/ladder", headers=auth()).status_code == 409


def test_requires_login(client):
    assert client.get("/api/ladder").status_code == 401


def test_metadata_and_counts(client, loaded):
    md = ladder(client)["metadata"]
    assert md["calculation_date"] == "2026-10-10" and md["as_of_date"] == "2026-10-10"
    assert (md["transactions_read"], md["transactions_in_ladder"], md["past_due_count"]) == (1856, 1856, 0)
    assert md["scope_location"] is None


def test_currency_totals_match_golden_to_the_cent(client, loaded):
    rows = [r for r in ladder(client)["ladder"] if r["scope"] == "currency_total"]
    assert len(rows) == len(GOLDEN["by_currency_bucket"])
    for g in GOLDEN["by_currency_bucket"]:
        r = next(x for x in rows if x["currency"] == g["currency"] and x["bucket"] == g["bucket"])
        assert Decimal(str(r["asset_cash_at_maturity"])) == Decimal(g["assets"]), g
        assert Decimal(str(r["liability_cash_at_maturity"])) == Decimal(g["liabilities"]), g
        assert Decimal(str(r["net_gap"])) == Decimal(g["net_gap"]), g


def test_every_currency_location_cell_matches_golden(client, loaded):
    rows = [r for r in ladder(client)["ladder"] if r["scope"] == "location"]
    for g in GOLDEN["cells"]:
        r = next(x for x in rows if (x["currency"], x["location"], x["bucket"]) == (g["currency"], g["location"], g["bucket"]))
        assert Decimal(str(r["net_gap"])) == Decimal(g["net_gap"]), g
        assert (r["asset_count"], r["liability_count"]) == (g["n_assets"], g["n_liabilities"]), g


def test_boundary_transactions_land_in_the_expected_bucket(client, loaded):
    for case in GOLDEN["boundary_cases"][:20]:
        e = client.get(f"/api/ladder/explain/{case['id']}", headers=auth()).json()
        assert e["bucket"] == case["expected_bucket"], case


def test_drilldown_adds_up_to_the_cell(client, loaded):
    cell = next(r for r in ladder(client)["ladder"]
                if (r["currency"], r["scope"], r["bucket"]) == ("EUR", "currency_total", "3-12M"))
    d = client.get("/api/ladder/transactions", params={"currency": "EUR", "bucket": "3-12M", "page_size": 500},
                   headers=auth()).json()
    assert d["summary"]["net_gap"] == cell["net_gap"] and d["total"] == cell["asset_count"] + cell["liability_count"]


def test_explain_and_unknown_id(client, loaded):
    e = client.get("/api/ladder/explain/AST-006273", headers=auth()).json()
    assert [s["n"] for s in e["steps"]] == [1, 2, 3, 4, 5, 6] and e["stored_interest_matches_recomputation"]
    r = client.get("/api/ladder/explain/AST-007026", headers=auth())     # rejected at ingest
    assert r.status_code == 404 and r.json()["error"]["code"] == "TRANSACTION_NOT_FOUND"


def test_past_due_policy_with_a_later_calculation_date(client, loaded):
    base = ladder(client, calc_date="2027-03-01")
    assert base["metadata"]["past_due_count"] > 0 and base["warnings"]
    n = base["metadata"]["transactions_in_ladder"]
    inc = ladder(client, calc_date="2027-03-01", past_due_policy="bucket_0_1m")
    assert inc["metadata"]["transactions_in_ladder"] == n + base["metadata"]["past_due_count"]
    pd = client.get("/api/ladder/transactions", params={"bucket": "past_due", "calc_date": "2027-03-01"},
                    headers=auth()).json()
    assert pd["total"] == base["metadata"]["past_due_count"]


def test_standard_user_sees_only_own_location_and_totals_are_scoped(client, loaded):
    full = ladder(client)["ladder"]
    mine = ladder(client, headers=auth("standard", "Singapore"))
    assert mine["metadata"]["scope_location"] == "Singapore"
    assert {r["location"] for r in mine["ladder"]} <= {"Singapore", None}
    for r in mine["ladder"]:
        if r["scope"] == "currency_total":      # roll-up equals the Singapore cells only
            same = [x for x in full if (x["currency"], x["location"], x["bucket"]) == (r["currency"], "Singapore", r["bucket"])]
            assert r["net_gap"] == (same[0]["net_gap"] if same else 0)
    d = client.get("/api/ladder/transactions", params={"page_size": 500}, headers=auth("standard", "Singapore")).json()
    assert {t["location"] for t in d["items"]} == {"Singapore"}
    other = [t for t in client.get("/api/ladder/transactions", params={"location": "Paris"}, headers=auth()).json()["items"]]
    r = client.get(f"/api/ladder/explain/{other[0]['transaction_id']}", headers=auth("standard", "Singapore"))
    assert r.status_code == 404


def test_standard_user_without_location_is_refused(client, loaded):
    r = client.get("/api/ladder", headers=auth("standard", None))
    assert r.status_code == 403 and r.json()["error"]["code"] == "LOCATION_NOT_ASSIGNED"


@pytest.mark.parametrize("params", [{"currency": "JPY"}, {"bucket": "9Y"}, {"sort": "colour"}, {"type": "x"}])
def test_bad_filters_are_422(client, loaded, params):
    assert client.get("/api/ladder/transactions", params=params, headers=auth()).status_code == 422


def test_reupload_replaces_snapshot_and_result_is_deterministic(client, loaded):
    a = ladder(client)["ladder"]
    upload(client, "asset")                   # same date -> old batch deactivated
    assert ladder(client)["ladder"] == a
