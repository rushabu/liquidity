from datetime import date
from decimal import Decimal

import pytest

from app.services.validation import p_date, p_rating, validate
from .helpers import BASE, make, rules, run


# ----------------------------------------------------------- positive cases
@pytest.mark.parametrize("book", ["asset", "liability", "historical"])
def test_valid_row_is_stored_without_issues(book):
    res = run(book)
    assert len(res.valid_rows) == 1 and res.issues == []


def test_asset_row_gets_interest_and_cash_at_maturity():
    row = run("asset").valid_rows[0]
    assert row["tenor_days"] == 183
    assert row["interest_amount"] == Decimal("25068.49")
    assert row["cash_at_maturity"] == Decimal("1025068.49")
    assert row["loan_amount"] == Decimal("1000000.00") and "amount" not in row


def test_liability_row_has_amount_column():
    row = run("liability").valid_rows[0]
    assert row["amount"] == Decimal("250000.00") and row["cash_at_maturity"] > row["amount"]


def test_historical_blank_closure_is_open_contract_with_null_flag():
    res = run("historical", Actual_Closure_Date="")
    assert len(res.valid_rows) == 1 and res.valid_rows[0]["is_early_exit"] is None


def test_historical_early_exit_flag_true_and_false():
    assert run("historical").valid_rows[0]["is_early_exit"] is True                    # closed before maturity
    on_time = run("historical", Actual_Closure_Date="2026-01-10").valid_rows[0]
    assert on_time["is_early_exit"] is False                                           # closed at maturity


def test_historical_type_is_case_insensitive_and_normalised():
    assert run("historical", Type="lIaBiLiTy", Transaction_ID="LIA-000009",
               Product_Type="Product-A").valid_rows[0]["type"] == "Liability"


def test_excel_datetime_and_whole_number_float_are_accepted():
    from datetime import datetime
    assert p_date(datetime(2026, 10, 10, 0, 0)) == date(2026, 10, 10)
    assert p_rating(3.0) == 3 and p_rating("4") == 4


def test_maturity_equal_to_transaction_date_is_allowed():
    res = run("asset", Maturity_Date="2026-06-20")
    assert len(res.valid_rows) == 1 and res.valid_rows[0]["interest_amount"] == Decimal("0.00")


def test_boundary_values_are_accepted():
    res = run("asset", Interest_Rate_Pct="0", Market_Interest_Rate_Pct="100", Client_Rating="1", Loan_Amount="0.01")
    assert len(res.valid_rows) == 1


# ------------------------------------------------- negative case per rule code
NEGATIVE = [
    # (book, column, bad value, expected rule code)
    ("asset", "Transaction_ID", "AST-12A", "ID_PATTERN"),
    ("asset", "Transaction_ID", "LIA-000001", "ID_PATTERN"),
    ("liability", "Transaction_ID", "AST-000001", "ID_PATTERN"),
    ("historical", "Transaction_ID", "XYZ-000001", "ID_PATTERN"),
    ("asset", "Entity", "IBC Mars", "ENTITY_INVALID"),
    ("asset", "Entity", "ibc uk", "ENTITY_INVALID"),                 # exact match, no auto-correct
    ("asset", "Location", "Mumbai", "LOCATION_INVALID"),
    ("asset", "Customer_ID", "CUST-1", "CUSTOMER_ID_PATTERN"),
    ("asset", "Loan_Amount", "0", "AMOUNT_INVALID"),
    ("asset", "Loan_Amount", "-5", "AMOUNT_INVALID"),
    ("asset", "Loan_Amount", "1,000,000", "AMOUNT_INVALID"),         # no thousands separators
    ("asset", "Loan_Amount", "abc", "AMOUNT_INVALID"),
    ("liability", "Amount", "0", "AMOUNT_INVALID"),
    ("asset", "Currency", "US", "CURRENCY_INVALID"),
    ("liability", "Currency", "EURO", "CURRENCY_INVALID"),
    ("asset", "Currency", "usd", "CURRENCY_INVALID"),
    ("asset", "Transaction_Date", "32/13/2026", "DATE_FORMAT"),
    ("asset", "Transaction_Date", "2026-02-30", "DATE_FORMAT"),      # not a real date
    ("asset", "Transaction_Date", "20/06/2026", "DATE_FORMAT"),
    ("asset", "Maturity_Date", "2026-13-01", "DATE_FORMAT"),
    ("asset", "Maturity_Date", "2026-06-19", "MATURITY_BEFORE_TXN"),
    ("liability", "Maturity_Date", "2026-08-31", "MATURITY_BEFORE_TXN"),
    ("asset", "Interest_Rate_Pct", "100.5", "RATE_INVALID"),
    ("asset", "Interest_Rate_Pct", "-0.1", "RATE_INVALID"),
    ("asset", "Interest_Rate_Pct", "5%", "RATE_INVALID"),
    ("asset", "Market_Interest_Rate_Pct", "101", "MARKET_RATE_INVALID"),
    ("asset", "Loan_Type", "Product-A", "PRODUCT_INVALID"),
    ("liability", "Product_Type", "Loan-Type-A", "PRODUCT_INVALID"),
    ("historical", "Product_Type", "Product-Z", "PRODUCT_INVALID"),
    ("asset", "Customer_Segment", "retail", "SEGMENT_INVALID"),
    ("asset", "Client_Rating", "0", "RATING_INVALID"),
    ("asset", "Client_Rating", "6", "RATING_INVALID"),
    ("asset", "Client_Rating", "3.5", "RATING_INVALID"),
    ("historical", "Type", "Deposit", "TYPE_INVALID"),
    ("asset", "Customer_ID", "", "REQUIRED_BLANK"),
    ("liability", "Currency", "   ", "REQUIRED_BLANK"),
    ("historical", "Contractual_Maturity_Date", "", "REQUIRED_BLANK"),
]


@pytest.mark.parametrize("book,col,bad,rule", NEGATIVE)
def test_negative_rule(book, col, bad, rule):
    res = run(book, **{col: bad})
    assert rule in rules(res), res.issues
    assert res.valid_rows == [] and res.rows_rejected == 1


def test_entity_location_mismatch():
    res = run("asset", Entity="IBC UK", Location="Paris")
    assert rules(res) == ["ENTITY_LOCATION_MISMATCH"]


def test_mapping_not_checked_when_entity_is_invalid():
    assert rules(run("asset", Entity="HBC HK", Location="Singapore")) == ["ENTITY_INVALID"]


def test_one_row_can_report_several_rules():
    res = run("asset", Currency="US", Client_Rating="9", Loan_Amount="-1")
    assert sorted(rules(res)) == ["AMOUNT_INVALID", "CURRENCY_INVALID", "RATING_INVALID"]


def test_historical_blank_closure_but_other_blank_fields_still_required():
    assert "REQUIRED_BLANK" in rules(run("historical", Actual_Closure_Date="", Amount=""))


def test_historical_bad_closure_date_is_rejected():
    assert rules(run("historical", Actual_Closure_Date="31/12/2025")) == ["DATE_FORMAT"]


# --------------------------------------------- historical warnings (row kept)
def test_historical_maturity_before_txn_is_a_warning_and_row_is_stored():
    res = run("historical", Contractual_Maturity_Date="2024-12-01")
    assert len(res.valid_rows) == 1 and rules(res, "warning") == ["HIST_MATURITY_BEFORE_TXN"] and not res.errors


def test_historical_closure_before_txn_is_a_warning():
    res = run("historical", Actual_Closure_Date="2024-12-31")
    assert len(res.valid_rows) == 1 and rules(res, "warning") == ["HIST_CLOSURE_BEFORE_TXN"]


def test_type_id_prefix_mismatch_is_a_warning():
    res = run("historical", Type="Liability", Product_Type="Product-A")
    assert len(res.valid_rows) == 1 and rules(res, "warning") == ["TYPE_ID_PREFIX_MISMATCH"]


def test_live_books_do_not_get_the_historical_warnings():
    assert rules(run("liability", Maturity_Date="2026-01-01")) == ["MATURITY_BEFORE_TXN"]


# ------------------------------------------------------------- duplicates
def test_duplicate_transaction_id_keeps_first_and_names_it():
    parsed = make("asset", [{}, {"Loan_Amount": "7"}])               # same ID, different data
    res = validate("asset", parsed)
    assert len(res.valid_rows) == 1 and res.valid_rows[0]["loan_amount"] == Decimal("1000000.00")
    dup = res.errors[0]
    assert dup.rule_code == "ID_DUPLICATE" and dup.source_row == 3 and "row 2" in dup.message


def test_whole_row_duplicate_is_rejected_once_and_first_is_kept():
    res = validate("asset", make("asset", [{}, {}, {"Transaction_ID": "AST-000002"}]))
    assert [i.rule_code for i in res.errors] == ["ROW_DUPLICATE"]
    assert len(res.valid_rows) == 2 and res.issues[0].source_row == 3


def test_distinct_rows_are_all_kept():
    res = validate("asset", make("asset", [{"Transaction_ID": f"AST-{i:06d}"} for i in range(1, 6)]))
    assert len(res.valid_rows) == 5 and not res.issues


def test_row_numbers_match_the_users_file_with_blank_lines_in_between():
    from app.services.parsing import parse_file
    csv_bytes = ("Transaction_ID,Entity\n" + "AST-1,x\n\n\nAST-2,y\n").encode()
    parsed = parse_file(csv_bytes, "a.csv")
    assert [n for n, _ in parsed.rows] == [2, 5]


# ------------------------------------------------------- extra / large values
def test_extra_columns_are_ignored_with_a_warning():
    parsed = make("asset", [{}])
    parsed.headers.append("Notes")
    parsed.rows[0][1]["Notes"] = "hello"
    res = validate("asset", parsed)
    assert len(res.valid_rows) == 1 and rules(res, "warning") == ["EXTRA_COLUMN"]


def test_values_too_large_for_the_database_are_rejected_not_crashed():
    assert "AMOUNT_INVALID" in rules(run("asset", Loan_Amount="9" * 25))
    assert "AMOUNT_INVALID" in rules(run("asset", Loan_Amount="9" * 17, Interest_Rate_Pct="100",
                                         Transaction_Date="0001-01-01", Maturity_Date="9999-12-31"))


def test_tiny_amount_that_rounds_to_zero_is_rejected():
    assert rules(run("asset", Loan_Amount="0.001")) == ["AMOUNT_INVALID"]
