import csv
import io

from app.services.parsing import parse_file
from app.services.validation import SPECS, validate

BASE = {
    "asset": {"Transaction_ID": "AST-000001", "Entity": "IBC UK", "Location": "London", "Customer_ID": "CUS-00001",
              "Loan_Amount": "1000000", "Currency": "EUR", "Transaction_Date": "2026-06-20",
              "Maturity_Date": "2026-12-20", "Interest_Rate_Pct": "5", "Market_Interest_Rate_Pct": "5.25",
              "Loan_Type": "Loan-Type-A", "Customer_Segment": "Corporate", "Client_Rating": "3"},
    "liability": {"Transaction_ID": "LIA-000001", "Entity": "IBC SA", "Location": "Paris", "Customer_ID": "CUS-00002",
                  "Amount": "250000", "Currency": "USD", "Transaction_Date": "2026-09-01",
                  "Maturity_Date": "2027-03-01", "Interest_Rate_Pct": "3.5", "Market_Interest_Rate_Pct": "3.5",
                  "Product_Type": "Product-B", "Customer_Segment": "Retail", "Client_Rating": "2"},
    "historical": {"Transaction_ID": "AST-000009", "Entity": "IBC USA", "Type": "Asset", "Location": "New York",
                   "Transaction_Date": "2025-01-10", "Customer_ID": "CUS-00003", "Customer_Segment": "SME",
                   "Client_Rating": "4", "Amount": "500000", "Currency": "GBP", "Interest_Rate_Pct": "4.5",
                   "Market_Interest_Rate_Pct": "5.5", "Product_Type": "Loan-Type-C",
                   "Contractual_Maturity_Date": "2026-01-10", "Actual_Closure_Date": "2025-11-01"},
}


def make(book, rows):
    """rows: list of dicts (merged over BASE[book]); returns ParsedFile via the real CSV reader."""
    headers = list(BASE[book])
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(headers)
    for r in rows:
        merged = {**BASE[book], **r}
        w.writerow([merged[h] for h in headers])
    return parse_file(buf.getvalue().encode(), f"{book}.csv")


def run(book, **overrides):
    return validate(book, make(book, [overrides]))


def rules(res, severity="error"):
    return [i.rule_code for i in res.issues if i.severity == severity]
