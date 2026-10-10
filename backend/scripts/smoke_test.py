"""End-to-end check of a RUNNING Liquid+ API (local, Docker or deployed) with the three sample files.

    python -m scripts.smoke_test --token "$SUPPORT_TOKEN"
    python -m scripts.smoke_test --token "$SUPPORT_TOKEN" --standard-token "$SINGAPORE_TOKEN"
    python -m scripts.smoke_test --api http://localhost:8000          # only with DEV_AUTH_BYPASS=true

It uploads the asset book, liability book and historical log, compares the counts with the expected
numbers, then checks replacement, the summary, the error report, the CSV, and the auth rules.
NOTE: it replaces the active 2026-10-10 snapshot in that database.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

DATA = Path(__file__).resolve().parent.parent / "tests" / "data"
GOLDEN = {"asset": (755, 753, 2, 0), "liability": (1104, 1103, 1, 0), "historical": (16186, 16179, 7, 1)}
FILES = {"asset": "asset_book", "liability": "liability_book", "historical": "historical_log"}


def run(api: str, token: str | None, standard_token: str | None, date: str = "2026-10-10") -> list[tuple[str, bool, str]]:
    out: list[tuple[str, bool, str]] = []
    ddmmyyyy = f"{date[8:10]}{date[5:7]}{date[0:4]}"

    def check(name: str, ok: bool, detail: str = "") -> None:
        out.append((name, ok, detail))
        print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   ({detail})" if detail else ""), flush=True)

    h = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=api.rstrip("/"), timeout=120) as c:
        r = c.get("/health"); check("GET /health", r.status_code == 200, r.text[:60])
        r = c.get("/health/db"); check("GET /health/db (database reachable)", r.status_code == 200, r.text[:80])
        if r.status_code != 200:
            return out
        check("garbage token is rejected (401)", c.get("/api/ingest/summary", headers={"Authorization": "Bearer x.y.z"}).status_code == 401)

        ids: dict[str, str] = {}
        for book, stem in FILES.items():
            path = DATA / f"{stem}.xlsx"
            r = c.post(f"/api/ingest/{book}", headers=h, data={"as_of_date": date},
                       files={"file": (f"{stem} - {ddmmyyyy}.xlsx", path.read_bytes())})
            if r.status_code != 201:
                check(f"upload {book}", False, f"HTTP {r.status_code}: {r.text[:150]}")
                continue
            b = r.json(); ids[book] = b["batch_id"]
            got = (b["rows_read"], b["rows_valid"], b["rows_rejected"], b["warnings"])
            check(f"upload {book}: read/stored/rejected/warnings", got == GOLDEN[book], f"{got} expected {GOLDEN[book]}, {b['duration_ms']} ms")

        s = c.get("/api/ingest/summary", headers=h, params={"as_of_date": date}).json()
        check("summary: all three books ready", s.get("ready") is True and s.get("rows_stored_total") == 18035,
              f"ready={s.get('ready')} total={s.get('rows_stored_total')}")

        if "asset" in ids:
            r = c.post("/api/ingest/asset", headers=h, data={"as_of_date": date},
                       files={"file": (f"asset_book - {ddmmyyyy}.xlsx", (DATA / "asset_book.xlsx").read_bytes())})
            check("re-upload replaces the previous batch (no duplicates)",
                  r.status_code == 201 and r.json().get("replaced_batch_id") == ids["asset"])

        e = c.get("/api/ingest/errors", headers=h, params={"book": "historical"}).json()
        check("error report: 7 rejects + 1 warning for the historical log", e.get("total") == 8, f"total={e.get('total')}")
        r = c.get("/api/ingest/errors.csv", headers=h, params={"batch_ids": ",".join(ids.values())})
        check("error CSV downloads", r.status_code == 200 and r.text.lstrip("﻿").startswith("file,source_row"))

        bad = c.post("/api/ingest/liability", headers=h, data={"as_of_date": date},
                     files={"file": ("x - 10102026.xlsx", (DATA / "asset_book.xlsx").read_bytes())})
        check("asset file sent to liability endpoint -> 422 FILE_WRONG_BOOK",
              bad.status_code == 422 and bad.json()["error"]["code"] == "FILE_WRONG_BOOK")

        if standard_token:
            sh = {"Authorization": f"Bearer {standard_token}"}
            r = c.post("/api/ingest/asset", headers=sh, files={"file": ("a - 10102026.xlsx", b"x")})
            check("standard user cannot upload (403)", r.status_code == 403, f"HTTP {r.status_code}")
            check("standard user can read the summary (200)", c.get("/api/ingest/summary", headers=sh).status_code == 200)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--token", default=os.environ.get("LIQUIDPLUS_TOKEN"), help="Support user's access token")
    ap.add_argument("--standard-token", default=os.environ.get("LIQUIDPLUS_STANDARD_TOKEN"))
    a = ap.parse_args()
    try:
        results = run(a.api, a.token, a.standard_token)
    except httpx.HTTPError as e:
        print(f"Cannot reach {a.api}: {e.__class__.__name__}. Is the API running?", file=sys.stderr)
        return 2
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
