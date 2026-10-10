import csv
import io
import json
import logging
import uuid

import pytest
from sqlalchemy import text

from tests.integration.conftest import FILES, auth, count, upload

GOLDEN = {
    "asset": dict(rows_read=755, rows_valid=753, rows_rejected=2, warnings=0),
    "liability": dict(rows_read=1104, rows_valid=1103, rows_rejected=1, warnings=0),
    "historical": dict(rows_read=16186, rows_valid=16179, rows_rejected=7, warnings=1),
}
TABLE = {"asset": "assets", "liability": "liabilities", "historical": "historical_log"}


# ------------------------------------------------------------ golden numbers
@pytest.mark.parametrize("book", ["asset", "liability", "historical"])
def test_real_file_golden_numbers(client, db, book):
    r = upload(client, book)
    assert r.status_code == 201, r.text
    body = r.json()
    for k, v in GOLDEN[book].items():
        assert body[k] == v, (k, body[k])
    assert body["as_of_date"] == "2026-10-10" and body["filename"] == FILES[book]
    assert body["replaced_batch_id"] is None
    assert count(db, f"select count(*) from {TABLE[book]} where batch_id = :b", b=body["batch_id"]) == GOLDEN[book]["rows_valid"]
    assert count(db, "select count(*) from ingestion_errors where batch_id = :b and severity='error'",
                 b=body["batch_id"]) == GOLDEN[book]["rows_rejected"]


def test_historical_rejects_and_warning_exactly_as_specified(client, db):
    body = upload(client, "historical").json()
    rows = db.execute(text("select source_row, transaction_id, rule_code, value, severity from ingestion_errors "
                           "where batch_id=:b order by source_row"), {"b": body["batch_id"]}).all()
    assert [tuple(r) for r in rows] == [
        (2389, "AST-001045", "CURRENCY_INVALID", "US", "error"),
        (4778, "LIA-002687", "TYPE_INVALID", "Deposit", "error"),
        (7166, "AST-003136", "AMOUNT_INVALID", "0", "error"),
        (9554, "AST-004181", "ENTITY_INVALID", "HBC HK", "error"),
        (11943, "LIA-006716", "DATE_FORMAT", "32/13/2026", "error"),
        (15083, "AST-007026", "CURRENCY_INVALID", "US", "error"),
        (15084, "AST-007027", "HIST_MATURITY_BEFORE_TXN", "2025-11-15", "warning"),
        (16187, "LIA-009162", "CURRENCY_INVALID", "EURO", "error")]


def test_asset_and_liability_rejects_as_specified(client, db):
    a, l = upload(client, "asset").json(), upload(client, "liability").json()
    get = lambda b: [tuple(r) for r in db.execute(text(
        "select source_row, transaction_id, rule_code, value from ingestion_errors where batch_id=:b order by 1"), {"b": b})]
    assert get(a["batch_id"]) == [(755, "AST-007026", "CURRENCY_INVALID", "US"),
                                  (756, "AST-007027", "MATURITY_BEFORE_TXN", "2025-11-15")]
    assert get(l["batch_id"]) == [(1105, "LIA-009162", "CURRENCY_INVALID", "EURO")]


def test_rejected_rows_are_absent_and_warning_row_is_present(client, db):
    upload(client, "asset"); upload(client, "historical")
    assert count(db, "select count(*) from assets where transaction_id in ('AST-007026','AST-007027')") == 0
    assert count(db, "select count(*) from historical_log where transaction_id in "
                     "('AST-001045','LIA-002687','AST-003136','AST-004181','LIA-006716','AST-007026','LIA-009162')") == 0
    assert count(db, "select count(*) from historical_log where transaction_id = 'AST-007027'") == 1


def test_every_stored_live_row_has_interest_and_cash_at_maturity(client, db):
    upload(client, "asset"); upload(client, "liability")
    for t in ("assets", "liabilities"):
        assert count(db, f"select count(*) from {t} where interest_amount is null or cash_at_maturity is null "
                         f"or tenor_days is null") == 0
        assert count(db, f"select count(*) from {t} where cash_at_maturity <> "
                         f"{'loan_amount' if t == 'assets' else 'amount'} + interest_amount") == 0
    # spot check one row by hand: AST-006273 USD 9,056,000 @ 4.8554% for 1471 days
    row = db.execute(text("select tenor_days, interest_amount, cash_at_maturity from assets "
                          "where transaction_id='AST-006273'")).one()
    assert row[0] == 1471 and str(row[1]) == "1772071.48" and str(row[2]) == "10828071.48"


def test_historical_early_exit_flag_stored(client, db):
    upload(client, "historical")
    total = count(db, "select count(*) from historical_log")
    opened = count(db, "select count(*) from historical_log where actual_closure_date is null")
    early = count(db, "select count(*) from historical_log where is_early_exit")
    assert count(db, "select count(*) from historical_log where is_early_exit is null") == opened   # open => NULL
    assert count(db, "select count(*) from historical_log where is_early_exit is not null and "
                     "is_early_exit <> (actual_closure_date < contractual_maturity_date)") == 0
    assert 0.25 < early / total < 0.40, early / total          # use case: ~30-35% early exits


def test_performance_16k_rows_under_10_seconds(client):
    body = upload(client, "historical").json()
    assert body["duration_ms"] < 10_000, body["duration_ms"]


def test_50k_rows_are_supported(client, db):
    from tests.unit.helpers import BASE
    buf = io.StringIO(); w = csv.writer(buf); headers = list(BASE["asset"]); w.writerow(headers)
    for i in range(50_000):
        r = {**BASE["asset"], "Transaction_ID": f"AST-{i + 1:07d}"}
        w.writerow([r[h] for h in headers])
    r = upload(client, "asset", name="asset_book - 10102026.csv", content=buf.getvalue().encode())
    assert r.status_code == 201, r.text
    assert r.json()["rows_valid"] == 50_000 and r.json()["duration_ms"] < 30_000, r.json()["duration_ms"]
    assert count(db, "select count(*) from assets") == 50_000


# ------------------------------------------------------------- idempotency
def test_same_file_twice_leaves_one_active_batch_and_no_duplicates(client, db):
    first = upload(client, "asset").json()
    second = upload(client, "asset").json()
    assert second["replaced_batch_id"] == first["batch_id"] and second["batch_id"] != first["batch_id"]
    active = db.execute(text("select id from ingestion_batches where book='asset' and is_active")).scalars().all()
    assert [str(a) for a in active] == [second["batch_id"]]
    assert count(db, "select count(*) from ingestion_batches where book='asset' and not is_active") == 1
    # active data has no duplicates: one row per transaction_id
    assert count(db, "select count(*) from assets a join ingestion_batches b on b.id=a.batch_id where b.is_active") == 753
    assert count(db, "select count(distinct a.transaction_id) from assets a join ingestion_batches b "
                     "on b.id=a.batch_id where b.is_active") == 753


def test_different_snapshot_dates_stay_active_side_by_side(client, db):
    upload(client, "asset")
    upload(client, "asset", name="asset_book - 11102026.xlsx")
    assert count(db, "select count(*) from ingestion_batches where is_active") == 2


def test_as_of_date_precedence_form_then_filename_then_today(client):
    assert upload(client, "liability", data={"as_of_date": "2026-01-31"}).json()["as_of_date"] == "2026-01-31"
    assert upload(client, "liability").json()["as_of_date"] == "2026-10-10"
    from datetime import date
    assert upload(client, "liability", name="liabilities.xlsx").json()["as_of_date"] == date.today().isoformat()
    r = upload(client, "liability", data={"as_of_date": "2026-02-30"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "AS_OF_DATE_INVALID"


# --------------------------------------------------------------- atomicity
def _failing_copy(monkeypatch, fail_on_call: int):
    from app.config import get_settings
    from app.services import ingestion
    monkeypatch.setattr(get_settings(), "insert_chunk_size", 100)
    real, calls = ingestion._copy, {"n": 0}

    def flaky(cur, table, columns, rows):
        calls["n"] += 1
        if calls["n"] == fail_on_call:
            raise RuntimeError("simulated database failure")
        return real(cur, table, columns, rows)
    monkeypatch.setattr(ingestion, "_copy", flaky)
    return calls


def test_failure_mid_insert_persists_nothing_and_leaves_no_active_batch(client, db, monkeypatch):
    calls = _failing_copy(monkeypatch, fail_on_call=3)          # 3rd chunk of 100 rows fails
    r = client.post("/api/ingest/asset", files={"file": ("asset_book - 10102026.xlsx",
                    open("tests/data/asset_book.xlsx", "rb").read())}, headers=auth())
    assert r.status_code == 500 and calls["n"] == 3
    err = r.json()["error"]
    assert err["code"] == "INTERNAL_ERROR" and err["details"]["correlation_id"] == r.headers["x-request-id"]
    assert "simulated" not in r.text                             # no internals leaked
    assert count(db, "select count(*) from ingestion_batches") == 0
    assert count(db, "select count(*) from assets") == 0
    assert count(db, "select count(*) from ingestion_errors") == 0
    assert count(db, "select count(*) from ingestion_batches where is_active") == 0


def test_failure_keeps_the_previous_active_batch_intact(client, db, monkeypatch):
    first = upload(client, "asset").json()
    _failing_copy(monkeypatch, fail_on_call=2)
    r = upload(client, "asset")
    assert r.status_code == 500
    active = db.execute(text("select id from ingestion_batches where is_active")).scalars().all()
    assert [str(a) for a in active] == [first["batch_id"]]
    assert count(db, "select count(*) from assets") == 753


# ---------------------------------------------------------------- security
def test_non_support_user_cannot_upload(client, db):
    r = upload(client, "asset", headers=auth("standard", "London"))
    assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
    assert count(db, "select count(*) from ingestion_batches") == 0


def test_missing_or_bad_token_is_401(client):
    assert upload(client, "asset", headers={}).status_code == 401
    r = upload(client, "asset", headers={"Authorization": "Bearer not.a.jwt"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"


def test_tampered_token_signature_is_rejected(client):
    import jwt as pyjwt, time
    bad = pyjwt.encode({"sub": "x", "aud": "authenticated", "exp": int(time.time()) + 60,
                        "app_metadata": {"role": "support"}}, "wrong-secret-wrong-secret-wrong-secret!", "HS256")
    assert upload(client, "asset", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_standard_user_can_read_but_not_write(client):
    upload(client, "asset")
    h = auth("standard", "Paris")
    assert client.get("/api/ingest/summary", headers=h).status_code == 200
    assert client.get("/api/ingest/batches", headers=h).status_code == 200
    assert client.get("/api/ingest/summary").status_code == 401


def test_oversized_files_get_413_with_nothing_stored(client, db):
    assert upload(client, "asset", name="a.csv", content=b"x" * (26 * 1024 * 1024)).status_code == 413
    r = upload(client, "asset", name="a.csv", content=b"x" * (int(25.5 * 1024 * 1024)))    # past the limit, under the header guard
    assert r.status_code == 413 and r.json()["error"]["code"] == "FILE_TOO_LARGE"
    assert count(db, "select count(*) from ingestion_batches") == 0


def test_asset_file_sent_to_liability_endpoint_is_422_wrong_book(client, db):
    r = upload(client, "liability", name="asset_book - 10102026.xlsx",
               content=open("tests/data/asset_book.xlsx", "rb").read())
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "FILE_WRONG_BOOK" and r.json()["error"]["details"]["detected_book"] == "asset"
    assert count(db, "select count(*) from ingestion_batches") == 0


@pytest.mark.parametrize("name,content,status,code", [
    ("book.pdf", b"%PDF", 422, "FILE_TYPE_UNSUPPORTED"),
    ("a.csv", b"", 422, "FILE_EMPTY"),
    ("a.csv", b"Transaction_ID,Entity\nAST-1,IBC UK\n", 422, "FILE_MISSING_COLUMNS"),
    ("a.xlsx", b"not a zip", 422, "FILE_UNREADABLE"),
])
def test_file_level_failures_store_nothing(client, db, name, content, status, code):
    r = upload(client, "asset", name=name, content=content)
    assert r.status_code == status and r.json()["error"]["code"] == code
    assert set(r.json()["error"]) == {"code", "message", "details"}
    assert count(db, "select count(*) from ingestion_batches") == 0 and count(db, "select count(*) from assets") == 0
    assert count(db, "select count(*) from audit_log where action='INGEST_REJECTED'") == 1


# --------------------------------------------------------------- read APIs
def test_summary_ready_only_when_all_three_books_share_a_snapshot_date(client):
    h = auth("standard", "London")
    assert client.get("/api/ingest/summary", headers=h).json() == {
        "as_of_date": None, "books": {"asset": None, "liability": None, "historical": None},
        "rows_stored_total": 0, "ready": False}
    upload(client, "asset"); upload(client, "liability")
    s = client.get("/api/ingest/summary", headers=h).json()
    assert s["ready"] is False and s["books"]["historical"] is None and s["rows_stored_total"] == 753 + 1103
    upload(client, "historical", name="historical_log - 09102026.xlsx")                 # different date
    assert client.get("/api/ingest/summary", headers=h).json()["ready"] is False
    upload(client, "historical")
    s = client.get("/api/ingest/summary", headers=h).json()
    assert s["ready"] is True and s["as_of_date"] == "2026-10-10"
    assert s["rows_stored_total"] == 753 + 1103 + 16179
    assert client.get("/api/ingest/summary?as_of_date=2026-10-09", headers=h).json()["ready"] is False


def test_batches_history(client):
    upload(client, "asset"); upload(client, "asset")
    items = client.get("/api/ingest/batches?limit=5", headers=auth("standard", "Paris")).json()["items"]
    assert len(items) == 2 and items[0]["is_active"] is True and items[1]["is_active"] is False
    assert {"book", "as_of_date", "rows_valid", "rows_rejected", "warnings_count", "source_filename"} <= set(items[0])


def test_errors_pagination_filters_and_facets(client):
    ids = [upload(client, b).json()["batch_id"] for b in ("asset", "liability", "historical")]
    h = auth("standard", "Paris")
    allr = client.get("/api/ingest/errors", headers=h).json()
    assert allr["total"] == 2 + 1 + 8 and set(allr["rule_codes"]) >= {"CURRENCY_INVALID", "HIST_MATURITY_BEFORE_TXN"}
    p1 = client.get("/api/ingest/errors?page_size=4&page=1", headers=h).json()
    p3 = client.get("/api/ingest/errors?page_size=4&page=3", headers=h).json()
    assert len(p1["items"]) == 4 and len(p3["items"]) == 3
    only_cur = client.get("/api/ingest/errors?rule_code=CURRENCY_INVALID", headers=h).json()
    assert only_cur["total"] == 5 and {i["value"] for i in only_cur["items"]} == {"US", "EURO"}
    assert client.get("/api/ingest/errors?severity=warning", headers=h).json()["total"] == 1
    hist = client.get("/api/ingest/errors?book=historical", headers=h).json()
    assert hist["total"] == 8 and all(i["book"] == "historical" and i["file"] == FILES["historical"] for i in hist["items"])
    one = client.get(f"/api/ingest/errors?batch_id={ids[0]}", headers=h).json()
    assert one["total"] == 2
    assert client.get("/api/ingest/errors?batch_id=nope", headers=h).status_code == 422
    assert client.get("/api/ingest/errors?page_size=999", headers=h).status_code == 422


def test_errors_default_scope_ignores_replaced_batches(client):
    upload(client, "liability"); upload(client, "liability")
    assert client.get("/api/ingest/errors", headers=auth("standard", "Paris")).json()["total"] == 1


UI_HEADER = ["File", "Row", "Transaction", "Field", "Value found", "Rule broken", "Message", "Severity"]


def _csv(client, headers=None, **params):
    r = client.get("/api/ingest/errors.csv", params=params, headers=headers or auth())
    assert r.status_code == 200, r.text
    return r, list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))


def test_errors_csv_download(client):
    ids = [upload(client, b).json()["batch_id"] for b in ("asset", "historical")]
    r, rows = _csv(client, auth("standard", "Paris"), batch_ids=",".join(ids))
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert rows[0] == UI_HEADER
    assert len(rows) == 1 + 2 + 8
    assert {len(x) for x in rows} == {8}
    assert client.get("/api/ingest/errors.csv?batch_ids=", headers=auth()).status_code == 422


def test_errors_csv_is_the_on_screen_report(client):
    """Same scope, filters, order and cell text as GET /errors (what the table renders), just unpaged."""
    for b in ("asset", "historical"):
        upload(client, b)
    for params in ({}, {"book": "historical"}, {"severity": "error"}, {"severity": "warning"},
                   {"book": "asset", "rule_code": "ID_DUPLICATE"}):
        ui = client.get("/api/ingest/errors", params={**params, "page_size": 200}, headers=auth()).json()
        _, rows = _csv(client, **params)
        assert rows[0] == UI_HEADER and len(rows) - 1 == ui["total"], params
        show = lambda v: "\u2014" if v in (None, "") else str(v)
        expected = [[show(i["file"]), show(i["source_row"]), show(i["transaction_id"]), show(i["field"]),
                     show(i["value"]), i["rule_code"], show(i["message"]),
                     "Error (row rejected)" if i["severity"] == "error" else "Warning"] for i in ui["items"]]
        assert rows[1:] == expected, params
    _, rows = _csv(client)
    assert rows[1][0] == FILES["historical"]                 # newest upload first, like the table
    assert any(r[7] == "Warning" for r in rows[1:]) and any(r[7] == "Error (row rejected)" for r in rows[1:])


def test_csv_report_neutralises_formula_injection(client, db):
    from tests.unit.helpers import BASE
    headers = list(BASE["asset"])
    bad = {**BASE["asset"], "Currency": "=HYPERLINK(\"http://evil\")", "Customer_ID": "@SUM(A1)"}
    csv_text = ",".join(headers) + "\n" + ",".join('"' + bad[h].replace('"', '""') + '"' for h in headers) + "\n"
    b = upload(client, "asset", name="asset - 10102026.csv", content=csv_text.encode()).json()
    body = client.get(f"/api/ingest/errors.csv?batch_ids={b['batch_id']}", headers=auth()).content.decode("utf-8-sig")
    cells = [c for row in csv.reader(io.StringIO(body)) for c in row]
    assert not any(c.startswith(("=", "@", "+")) for c in cells)
    assert any(c.startswith("'=HYPERLINK") for c in cells)


def test_nul_bytes_in_text_cells_do_not_break_the_upload(client):
    from tests.unit.helpers import BASE
    headers = list(BASE["asset"])
    bad = {**BASE["asset"], "Currency": "US\x00D"}
    csv_text = ",".join(headers) + "\n" + ",".join(bad[h] for h in headers) + "\n"
    r = upload(client, "asset", name="asset - 10102026.csv", content=csv_text.encode())
    assert r.status_code == 201 and r.json()["rows_rejected"] == 1


# ------------------------------------------------------ audit, logs, schema
def test_audit_row_per_ingestion_with_actor_and_counts(client, db):
    upload(client, "liability", headers=auth("support"))
    row = db.execute(text("select actor, action, details from audit_log")).one()
    assert row[0] == "ops@bank.test" and row[1] == "INGEST_UPLOAD"
    assert row[2]["book"] == "liability" and row[2]["rows_valid"] == 1103 and row[2]["rows_rejected"] == 1


def test_info_logs_never_contain_customer_ids(client, caplog):
    caplog.set_level(logging.INFO)
    upload(client, "liability")
    text_out = "\n".join(r.getMessage() + json.dumps({k: str(v) for k, v in r.__dict__.items()}) for r in caplog.records
                         if r.levelno <= logging.INFO)
    assert "ingestion completed" in text_out and "CUS-" not in text_out


def test_every_data_table_has_rls_enabled_and_no_policies(db):
    rows = db.execute(text("select c.relname, c.relrowsecurity, "
                           "(select count(*) from pg_policy p where p.polrelid = c.oid) "
                           "from pg_class c where c.relnamespace='public'::regnamespace and c.relname in "
                           "('ingestion_batches','assets','liabilities','historical_log','ingestion_errors','audit_log')")).all()
    assert len(rows) == 6 and all(r[1] is True and r[2] == 0 for r in rows)


def test_database_check_constraints_back_up_the_rules(client, db):
    """Even if validation were bypassed, the database refuses rule-breaking rows."""
    from sqlalchemy.exc import IntegrityError
    upload(client, "asset")
    for col, bad in [("currency", "'US '"), ("loan_amount", "0"), ("client_rating", "9"),
                     ("transaction_id", "'XXX-1'"), ("location", "'Paris'"),      # IBC HongKong must be Hong Kong
                     ("maturity_date", "transaction_date - 1")]:
        with pytest.raises(IntegrityError):
            db.execute(text(f"update assets set {col} = {bad} where id = (select min(id) from assets)"))
        db.rollback()


def test_concurrent_uploads_of_the_same_snapshot_leave_exactly_one_active_batch(client, db):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as ex:
        results = list(ex.map(lambda _: upload(client, "liability"), range(4)))
    assert [r.status_code for r in results] == [201] * 4
    assert count(db, "select count(*) from ingestion_batches where book='liability' and is_active") == 1
    assert count(db, "select count(*) from ingestion_batches where book='liability'") == 4
    assert count(db, "select count(distinct a.transaction_id) from liabilities a join ingestion_batches b "
                     "on b.id=a.batch_id where b.is_active") == 1103
