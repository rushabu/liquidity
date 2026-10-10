"""Server-side validation of one parsed book.

Policy (from the use case): a broken rule rejects ONLY that row, never the
file. Every broken rule on a row is reported (a row can yield several error
lines). Values are never auto-corrected ("US" / "EURO" are rejected, not
turned into USD / EUR). The only normalisation is: surrounding whitespace is
stripped, and Historical `Type` is case-insensitive (-> Title case).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Callable

from ..core.liquidity_core import contract_cashflow
from .errors import ApiError
from .parsing import ParsedFile

# --------------------------------------------------------------------- enums
ENTITY_LOCATION = {
    "IBC HongKong": "Hong Kong", "IBC USA": "New York", "IBC UK": "London",
    "IBC Singapore": "Singapore", "IBC SA": "Paris",
}
LOCATIONS = set(ENTITY_LOCATION.values())
CURRENCIES = {"USD", "EUR", "GBP", "SGD", "HKD"}
SEGMENTS = {"Retail", "Corporate", "SME", "Institutional"}
LOAN_TYPES = {f"Loan-Type-{c}" for c in "ABCDE"}
PRODUCT_TYPES = {f"Product-{c}" for c in "ABCDE"}

_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_INT = re.compile(r"^-?\d+$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CUST = re.compile(r"^CUS-\d+$")
_MAX_AMOUNT = Decimal(10) ** 18          # numeric(20,2) headroom


@dataclass
class Issue:
    source_row: int | None
    transaction_id: str | None
    field: str | None
    value: str | None
    rule_code: str
    message: str
    severity: str = "error"          # error | warning


@dataclass
class ValidationResult:
    book: str
    rows_read: int
    valid_rows: list[dict] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    @property
    def rows_rejected(self) -> int:
        return self.rows_read - len(self.valid_rows)


class _Bad(Exception):
    def __init__(self, message: str):
        self.message = message


# ------------------------------------------------------------ cell parsers
def _text(v) -> str:
    if isinstance(v, (datetime, date)):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return "" if v is None else str(v).strip()


def _num(v) -> Decimal:
    if isinstance(v, bool):
        raise _Bad("boolean is not a number")
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            raise _Bad("not a finite number")
        return Decimal(repr(v))
    s = str(v).strip()
    if not _NUM.match(s):
        raise _Bad(f"'{s}' is not a plain number (no thousands separators or symbols)")
    return Decimal(s)


def p_pattern(rx: re.Pattern, hint: str):
    def f(v):
        s = _text(v)
        if not rx.match(s):
            raise _Bad(f"'{s}' does not match {hint}")
        return s
    return f


def p_enum(allowed) -> Callable:
    def f(v):
        s = _text(v)
        if s not in allowed:
            raise _Bad(f"'{s}' is not one of: {', '.join(sorted(allowed))}")
        return s
    return f


def p_amount(v) -> Decimal:
    d = _num(v).quantize(Decimal("0.01"))
    if d <= 0:
        raise _Bad("must be greater than 0")
    if d >= _MAX_AMOUNT:
        raise _Bad("exceeds the supported range")
    return d


def p_rate(v) -> Decimal:
    d = _num(v)
    if d < 0 or d > 100:
        raise _Bad("must be between 0 and 100")
    return d.quantize(Decimal("0.0001"))


def p_rating(v) -> int:
    if isinstance(v, bool):
        raise _Bad("boolean is not an integer")
    if isinstance(v, float):
        if not v.is_integer():
            raise _Bad(f"{v} is not an integer")
        i = int(v)
    elif isinstance(v, int):
        i = v
    else:
        s = str(v).strip()
        if not _INT.match(s):
            raise _Bad(f"'{s}' is not an integer")
        i = int(s)
    if not 1 <= i <= 5:
        raise _Bad("must be within 1 to 5")
    return i


def p_date(v) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        raise _Bad(f"'{v}' is a number, expected a YYYY-MM-DD date")
    s = _text(v)
    if not _ISO.match(s):
        raise _Bad(f"'{s}' is not in YYYY-MM-DD format")
    try:
        return date(int(s[:4]), int(s[5:7]), int(s[8:10]))
    except ValueError:
        raise _Bad(f"'{s}' is not a real calendar date") from None


def p_type(v) -> str:
    s = _text(v)
    if s.lower() not in ("asset", "liability"):
        raise _Bad(f"'{s}' is not one of: Asset, Liability")
    return s.title()


# ---------------------------------------------------------------- book specs
@dataclass(frozen=True)
class F:
    header: str
    db: str
    rule: str
    parse: Callable
    required: bool = True


def _common(id_rx: re.Pattern, id_hint: str, amount_header: str, product: F, maturity: F) -> list[F]:
    return [
        F("Transaction_ID", "transaction_id", "ID_PATTERN", p_pattern(id_rx, id_hint)),
        F("Entity", "entity", "ENTITY_INVALID", p_enum(set(ENTITY_LOCATION))),
        F("Location", "location", "LOCATION_INVALID", p_enum(LOCATIONS)),
        F("Customer_ID", "customer_id", "CUSTOMER_ID_PATTERN", p_pattern(_CUST, "CUS-<number>")),
        F(amount_header, "amount", "AMOUNT_INVALID", p_amount),
        F("Currency", "currency", "CURRENCY_INVALID", p_enum(CURRENCIES)),
        F("Transaction_Date", "transaction_date", "DATE_FORMAT", p_date),
        maturity,
        F("Interest_Rate_Pct", "interest_rate_pct", "RATE_INVALID", p_rate),
        F("Market_Interest_Rate_Pct", "market_rate_pct", "MARKET_RATE_INVALID", p_rate),
        product,
        F("Customer_Segment", "customer_segment", "SEGMENT_INVALID", p_enum(SEGMENTS)),
        F("Client_Rating", "client_rating", "RATING_INVALID", p_rating),
    ]


@dataclass(frozen=True)
class BookSpec:
    book: str
    fields: list[F]
    maturity_header: str
    maturity_db: str
    live: bool                 # live books get tenor / interest / cash at maturity

    @property
    def headers(self) -> list[str]:
        return [f.header for f in self.fields]


_ASSET = BookSpec(
    "asset",
    _common(re.compile(r"^AST-\d+$"), "AST-<number>", "Loan_Amount",
            F("Loan_Type", "loan_type", "PRODUCT_INVALID", p_enum(LOAN_TYPES)),
            F("Maturity_Date", "maturity_date", "DATE_FORMAT", p_date)),
    "Maturity_Date", "maturity_date", True)
_LIAB = BookSpec(
    "liability",
    _common(re.compile(r"^LIA-\d+$"), "LIA-<number>", "Amount",
            F("Product_Type", "product_type", "PRODUCT_INVALID", p_enum(PRODUCT_TYPES)),
            F("Maturity_Date", "maturity_date", "DATE_FORMAT", p_date)),
    "Maturity_Date", "maturity_date", True)
_HIST = BookSpec(
    "historical",
    _common(re.compile(r"^(AST|LIA)-\d+$"), "AST-<number> or LIA-<number>", "Amount",
            F("Product_Type", "product_type", "PRODUCT_INVALID", p_enum(LOAN_TYPES | PRODUCT_TYPES)),
            F("Contractual_Maturity_Date", "contractual_maturity_date", "DATE_FORMAT", p_date))
    + [F("Type", "type", "TYPE_INVALID", p_type),
       F("Actual_Closure_Date", "actual_closure_date", "DATE_FORMAT", p_date, required=False)],
    "Contractual_Maturity_Date", "contractual_maturity_date", False)

SPECS = {s.book: s for s in (_ASSET, _LIAB, _HIST)}


# ----------------------------------------------------------- file-level checks
def check_columns(book: str, headers: list[str]) -> list[str]:
    """Raises FILE_WRONG_BOOK / FILE_MISSING_COLUMNS; returns extra column names."""
    have = set(headers)
    spec = SPECS[book]
    missing = [h for h in spec.headers if h not in have]
    if missing:
        for other, ospec in SPECS.items():
            if other != book and all(h in have for h in ospec.headers):
                raise ApiError(422, "FILE_WRONG_BOOK",
                               f"This looks like a {other} book, but it was uploaded as {book}. "
                               f"Missing for {book}: {', '.join(missing)}.",
                               {"expected_book": book, "detected_book": other, "missing_columns": missing})
        raise ApiError(422, "FILE_MISSING_COLUMNS",
                       f"Missing required column(s): {', '.join(missing)}",
                       {"missing_columns": missing, "expected_columns": spec.headers})
    return [h for h in headers if h not in set(spec.headers)]


# ---------------------------------------------------------------- main entry
def _clip(v, n: int = 200) -> str | None:
    if v is None:
        return None
    s = _text(v)
    return s if len(s) <= n else s[: n - 1] + "…"


def _row_key(spec: BookSpec, raw: dict) -> tuple:
    return tuple(_text(raw.get(f.header)) for f in spec.fields)


def validate(book: str, parsed: ParsedFile) -> ValidationResult:
    spec = SPECS[book]
    extra = check_columns(book, parsed.headers)
    res = ValidationResult(book=book, rows_read=len(parsed.rows))
    for h in extra:
        res.issues.append(Issue(None, None, h, None, "EXTRA_COLUMN",
                                f"Column '{h}' is not part of the {book} schema and was ignored", "warning"))

    first_id_row: dict[str, int] = {}
    first_row_key: dict[tuple, int] = {}

    for source_row, raw in parsed.rows:
        tid = _clip(raw.get("Transaction_ID"), 40) or None
        errs: list[Issue] = []
        warns: list[Issue] = []
        vals: dict = {}

        def add(severity, header, rule, message, value=None):
            (errs if severity == "error" else warns).append(
                Issue(source_row, tid, header, _clip(value if value is not None else raw.get(header)),
                      rule, message, severity))

        # 1. cell level ------------------------------------------------------
        for f in spec.fields:
            cell = raw.get(f.header)
            if cell is None or (isinstance(cell, str) and cell == ""):
                if f.required:
                    add("error", f.header, "REQUIRED_BLANK", f"{f.header} is mandatory and is blank")
                vals[f.db] = None
                continue
            try:
                vals[f.db] = f.parse(cell)
            except _Bad as e:
                vals[f.db] = None
                add("error", f.header, f.rule, f"{f.header}: {e.message}")

        # 2. cross-field -----------------------------------------------------
        ent, loc = vals.get("entity"), vals.get("location")
        if ent and loc and ENTITY_LOCATION[ent] != loc:
            add("error", "Location", "ENTITY_LOCATION_MISMATCH",
                f"Entity '{ent}' must be booked in '{ENTITY_LOCATION[ent]}', found '{loc}'",
                value=f"{ent} / {loc}")
        txn, mat = vals.get("transaction_date"), vals.get(spec.maturity_db)
        if txn and mat and mat < txn:
            if spec.live:
                add("error", spec.maturity_header, "MATURITY_BEFORE_TXN",
                    f"{spec.maturity_header} {mat} is before Transaction_Date {txn}")
            else:   # historical sheet: no such rule in the use case -> warn, keep the row
                add("warning", spec.maturity_header, "HIST_MATURITY_BEFORE_TXN",
                    f"{spec.maturity_header} {mat} is before Transaction_Date {txn}")
        if not spec.live:
            closed = vals.get("actual_closure_date")
            if closed and txn and closed < txn:
                add("warning", "Actual_Closure_Date", "HIST_CLOSURE_BEFORE_TXN",
                    f"Actual_Closure_Date {closed} is before Transaction_Date {txn}")
            typ, t_id = vals.get("type"), vals.get("transaction_id")
            if typ and t_id and not t_id.startswith("AST-" if typ == "Asset" else "LIA-"):
                add("warning", "Type", "TYPE_ID_PREFIX_MISMATCH",
                    f"Type '{typ}' does not match the Transaction_ID prefix of '{t_id}'")

        # 3. duplicates (keep the FIRST occurrence) ----------------------------
        key = _row_key(spec, raw)
        if key in first_row_key:
            add("error", None, "ROW_DUPLICATE",
                f"Identical to row {first_row_key[key]}; the first occurrence is kept")
        else:
            first_row_key[key] = source_row
            t_id = vals.get("transaction_id")
            if t_id:
                if t_id in first_id_row:
                    add("error", "Transaction_ID", "ID_DUPLICATE",
                        f"Transaction_ID already used on row {first_id_row[t_id]}; the first occurrence is kept")
                else:
                    first_id_row[t_id] = source_row

        # 4. derived values (only for otherwise valid rows) ---------------------
        if not errs and spec.live:
            days, interest, cash = contract_cashflow(vals["amount"], vals["interest_rate_pct"], txn, mat)
            if cash >= _MAX_AMOUNT:
                add("error", spec.fields[4].header, "AMOUNT_INVALID",
                    "Principal plus interest exceeds the supported range")
            else:
                vals.update(tenor_days=days, interest_amount=interest, cash_at_maturity=cash)
        if not errs and book == "historical":
            closed = vals.get("actual_closure_date")
            vals["is_early_exit"] = None if closed is None else closed < mat

        res.issues.extend(errs)
        res.issues.extend(warns)
        if not errs:
            res.valid_rows.append(_to_db_row(book, vals))
    return res


def _to_db_row(book: str, v: dict) -> dict:
    """Rename the neutral 'amount' to the table's own column."""
    out = dict(v)
    if book == "asset":
        out["loan_amount"] = out.pop("amount")
    return out
