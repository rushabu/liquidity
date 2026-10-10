import io
from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from app.services.errors import ApiError
from app.services.parsing import as_of_from_filename, parse_file
from app.services.validation import check_columns, validate
from .helpers import BASE, make, rules, run


def code(exc: pytest.ExceptionInfo) -> str:
    return exc.value.code


# ------------------------------------------------------------ file-level
def test_unsupported_extension():
    with pytest.raises(ApiError) as e:
        parse_file(b"x", "book.pdf")
    assert code(e) == "FILE_TYPE_UNSUPPORTED" and e.value.status == 422


def test_empty_file_and_header_only_file():
    with pytest.raises(ApiError) as e:
        parse_file(b"", "a.csv")
    assert code(e) == "FILE_EMPTY"
    with pytest.raises(ApiError) as e:
        parse_file(b"Transaction_ID,Entity\n", "a.csv")
    assert code(e) == "FILE_EMPTY"


def test_corrupt_xlsx_is_reported_not_crashed():
    with pytest.raises(ApiError) as e:
        parse_file(b"this is not a zip", "a.xlsx")
    assert code(e) == "FILE_UNREADABLE"


def test_non_utf8_csv():
    with pytest.raises(ApiError) as e:
        parse_file("a,b\n\xe9,1".encode("latin-1"), "a.csv")
    assert code(e) == "FILE_UNREADABLE"


def test_missing_columns_are_listed():
    parsed = parse_file(b"Transaction_ID,Entity\nAST-1,IBC UK\n", "a.csv")
    with pytest.raises(ApiError) as e:
        validate("asset", parsed)
    assert code(e) == "FILE_MISSING_COLUMNS"
    assert "Loan_Amount" in e.value.details["missing_columns"] and "Currency" in e.value.details["missing_columns"]


@pytest.mark.parametrize("sent_as,actual", [("liability", "asset"), ("asset", "liability"),
                                            ("liability", "historical"), ("historical", "asset")])
def test_wrong_book_is_detected(sent_as, actual):
    with pytest.raises(ApiError) as e:
        validate(sent_as, make(actual, [{}]))
    assert code(e) == "FILE_WRONG_BOOK" and e.value.details["detected_book"] == actual


def test_column_order_is_irrelevant_and_headers_are_trimmed():
    headers = list(reversed(list(BASE["asset"])))
    csv_text = ",".join(f" {h} " for h in headers) + "\n" + ",".join(BASE["asset"][h] for h in headers) + "\n"
    res = validate("asset", parse_file(csv_text.encode(), "a.csv"))
    assert len(res.valid_rows) == 1


def test_csv_with_bom_is_accepted():
    headers = list(BASE["asset"])
    raw = ("﻿" + ",".join(headers) + "\n" + ",".join(BASE["asset"][h] for h in headers) + "\n").encode("utf-8")
    assert len(validate("asset", parse_file(raw, "a.csv")).valid_rows) == 1


# -------------------------------------------------------- xlsx typed cells
def _xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(list(BASE["asset"]))
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _asset_cells(**o):
    base = {**BASE["asset"], **o}
    return [base[h] for h in BASE["asset"]]


def test_excel_native_types_are_accepted():
    cells = _asset_cells(Loan_Amount=1000000, Transaction_Date=datetime(2026, 6, 20),
                         Maturity_Date=datetime(2026, 12, 20), Interest_Rate_Pct=5.0, Client_Rating=3)
    res = validate("asset", parse_file(_xlsx([cells]), "a.xlsx"))
    assert len(res.valid_rows) == 1 and str(res.valid_rows[0]["cash_at_maturity"]) == "1025068.49"


def test_excel_boolean_and_numeric_date_are_rejected():
    cells = _asset_cells(Loan_Amount=True, Transaction_Date=45000)
    res = validate("asset", parse_file(_xlsx([cells]), "a.xlsx"))
    assert sorted(rules(res)) == ["AMOUNT_INVALID", "DATE_FORMAT"]


def test_excel_blank_trailing_rows_are_ignored():
    res = validate("asset", parse_file(_xlsx([_asset_cells(), [None] * 13, [None] * 13]), "a.xlsx"))
    assert res.rows_read == 1


# --------------------------------------------------------- filename date
@pytest.mark.parametrize("name,expected", [
    ("asset_book - 10102026.xlsx", (2026, 10, 10)),
    ("liability_book_-_01022027.csv", (2027, 2, 1)),
    ("historical_log 31122025.xlsx", (2025, 12, 31)),
])
def test_as_of_date_from_filename(name, expected):
    assert as_of_from_filename(name).timetuple()[:3] == expected


@pytest.mark.parametrize("name", ["asset_book.xlsx", "asset_32132026.xlsx", "asset_101020261.xlsx", ""])
def test_as_of_date_absent_or_invalid(name):
    assert as_of_from_filename(name) is None


# -------------------------------------------------------- real files (fast)
DATA = Path(__file__).parent.parent / "data"


def test_real_asset_book_golden_numbers():
    p = DATA / "asset_book.xlsx"
    res = validate("asset", parse_file(p.read_bytes(), p.name))
    assert (res.rows_read, len(res.valid_rows), res.rows_rejected, len(res.warnings)) == (755, 753, 2, 0)
    assert [(i.source_row, i.transaction_id, i.rule_code, i.value) for i in res.errors] == [
        (755, "AST-007026", "CURRENCY_INVALID", "US"), (756, "AST-007027", "MATURITY_BEFORE_TXN", "2025-11-15")]
