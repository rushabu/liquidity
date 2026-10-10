from datetime import date
from decimal import Decimal

from app.core.liquidity_core import cash_at_maturity, contract_cashflow, simple_interest, tenor_days


def test_spec_example_eur_1m_at_5pct_183_days():
    days, interest, cash = contract_cashflow(Decimal("1000000"), Decimal("5"), date(2026, 6, 20), date(2026, 12, 20))
    assert days == 183
    assert interest == Decimal("25068.49")
    assert cash == Decimal("1025068.49")


def test_same_day_has_zero_interest():
    assert simple_interest(Decimal("500000"), Decimal("4.2"), 0) == Decimal("0.00")
    assert tenor_days(date(2026, 1, 1), date(2026, 1, 1)) == 0


def test_rounding_is_half_up():
    # 1000 * 1.5% * 1/365 = 0.041095... -> 0.04 ; 1000*10%*1/365=0.27397 -> 0.27
    assert simple_interest(Decimal("1000"), Decimal("1.5"), 1) == Decimal("0.04")
    assert cash_at_maturity(Decimal("100.00"), Decimal("0.005")) == Decimal("100.01")


def test_leap_year_uses_calendar_days_over_365():
    days = tenor_days(date(2028, 1, 1), date(2029, 1, 1))
    assert days == 366
    assert simple_interest(Decimal("365000"), Decimal("1"), days) == Decimal("3660.00")
