from datetime import date
from decimal import Decimal

import pytest

from app.core import ladder as L

CALC = date(2026, 10, 10)


def row(**kw):
    base = dict(transaction_id="AST-1", book="asset", location="London", currency="EUR", amount=Decimal("1000000"),
                transaction_date=date(2026, 6, 20), maturity_date=date(2026, 12, 20),
                interest_rate_pct=Decimal("5"), tenor_days=183, interest_amount=Decimal("25068.49"),
                cash_at_maturity=Decimal("1025068.49"))
    return {**base, **kw}


def test_add_months_clamps_month_end():
    assert L.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert L.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert L.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert L.add_months(date(2026, 10, 10), 60) == date(2031, 10, 10)


def test_bucket_edges_for_the_real_calc_date():
    b = L.build_buckets(CALC)
    assert [x.end for x in b[:-1]] == [date(2026, 11, 10), date(2027, 1, 10), date(2027, 10, 10), date(2031, 10, 10)]
    assert b[1].start == date(2026, 11, 11) and b[-1].end is None


@pytest.mark.parametrize("maturity,expected", [
    (date(2026, 10, 10), "0-1M"), (date(2026, 11, 10), "0-1M"), (date(2026, 11, 11), "1-3M"),
    (date(2027, 1, 10), "1-3M"), (date(2027, 1, 11), "3-12M"), (date(2027, 10, 10), "3-12M"),
    (date(2027, 10, 11), "1-5Y"), (date(2031, 10, 10), "1-5Y"), (date(2031, 10, 11), ">5Y"),
    (date(2026, 10, 9), None)])
def test_boundaries(maturity, expected):
    assert L.assign_bucket(maturity, L.build_buckets(CALC)) == expected


def test_past_due_policies():
    r = row(maturity_date=date(2026, 10, 1))
    ladder, past = L.classify([r], CALC)
    assert ladder == [] and len(past) == 1
    ladder, past = L.classify([r], CALC, "bucket_0_1m")
    assert ladder[0]["bucket"] == "0-1M" and ladder[0]["past_due"] and len(past) == 1
    with pytest.raises(ValueError):
        L.classify([r], CALC, "nope")


def test_net_gap_per_location_and_rollup_never_mixes_currencies():
    rows = [row(), row(transaction_id="L1", book="liability", cash_at_maturity=Decimal("400000"), amount=Decimal("390000")),
            row(transaction_id="A2", location="Paris", cash_at_maturity=Decimal("10")),
            row(transaction_id="U1", currency="USD", cash_at_maturity=Decimal("7"))]
    items, _ = L.classify(rows, CALC)
    out = L.aggregate(items, L.build_buckets(CALC))
    pick = lambda c, loc, b: next(r for r in out if (r["currency"], r["location"], r["bucket"]) == (c, loc, b))
    assert pick("EUR", "London", "1-3M")["net_gap"] == Decimal("625068.49")
    assert pick("EUR", None, "1-3M")["net_gap"] == Decimal("625078.49")
    assert pick("USD", None, "1-3M")["net_gap"] == Decimal("7")


def test_group_count():
    items, _ = L.classify([row(), row(transaction_id="U", currency="USD")], CALC)
    assert len(L.aggregate(items, L.build_buckets(CALC))) == 4 * 5   # 2 currencies x (London + total) x 5 buckets


def test_explain_matches_spec_example():
    e = L.explain_transaction(row(), CALC)
    assert e["bucket"] == "1-3M" and e["stored_interest_matches_recomputation"] is True
    assert [s["n"] for s in e["steps"]] == [1, 2, 3, 4, 5, 6]
    assert "183 days" in e["steps"][1]["result"] and "25,068.49" in e["steps"][2]["result"]


def test_explain_flags_tampered_interest():
    e = L.explain_transaction(row(interest_amount=Decimal("1.00")), CALC)
    assert e["stored_interest_matches_recomputation"] is False


def test_maturity_resolver_moves_bucket_not_interest():
    r = row()
    items, _ = L.classify([r], CALC, resolver=lambda _: date(2027, 6, 1))
    assert items[0]["bucket"] == "3-12M" and items[0]["cash_at_maturity"] == Decimal("1025068.49")
