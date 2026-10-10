"""Pure business functions shared by ingestion, the gap ladder and the ML
scoring. No I/O, no framework imports."""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

DAY_COUNT_BASIS = 365          # ACT/365, documented in the README assumptions
_TWO_DP = Decimal("0.01")


def tenor_days(transaction_date: date, maturity_date: date) -> int:
    """Calendar days between transaction and maturity."""
    return (maturity_date - transaction_date).days


def simple_interest(amount: Decimal, rate_pct: Decimal, days: int) -> Decimal:
    """interest = amount * rate/100 * days / 365, rounded to 2 dp (half up).
    Paid in full at maturity, no compounding (use-case guardrail)."""
    raw = amount * rate_pct / Decimal(100) * Decimal(days) / Decimal(DAY_COUNT_BASIS)
    return raw.quantize(_TWO_DP, rounding=ROUND_HALF_UP)


def cash_at_maturity(amount: Decimal, interest: Decimal) -> Decimal:
    return (amount + interest).quantize(_TWO_DP, rounding=ROUND_HALF_UP)


def contract_cashflow(amount: Decimal, rate_pct: Decimal, transaction_date: date,
                      maturity_date: date) -> tuple[int, Decimal, Decimal]:
    """Returns (tenor_days, interest_amount, cash_at_maturity)."""
    days = tenor_days(transaction_date, maturity_date)
    interest = simple_interest(amount, rate_pct, days)
    return days, interest, cash_at_maturity(amount, interest)
