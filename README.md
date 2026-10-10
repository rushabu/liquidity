# Liquid+ — Data Ingestion

Upload the three books (asset book, liability book, historical log), validate every row against the use-case rules, store the valid rows in Supabase Postgres, and report every rejected row. This is the first feature of Liquid+; the gap ladder and the ML model read from the tables created here.

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 + psycopg 3, openpyxl
- **Frontend:** React 18 + Vite + TypeScript, plain CSS tokens, IBM Plex (self-hosted)
- **Database:** Supabase Postgres, reached **only from the backend** (`DATABASE_URL`). The browser never talks to Supabase.

## Architecture

```
 Browser (React, /upload)
    │  multipart upload (XHR, progress)  ·  JSON reads  ·  CSV download
    ▼
 FastAPI ── routers/ingest.py ── services/parsing.py     file → raw rows (openpyxl read-only / csv, no inference)
    │                         ├─ services/validation.py   rules → valid rows + issues (errors / warnings)
    │                         ├─ services/ingestion.py    ONE transaction: batch → COPY rows → COPY issues
    │                         │                           → deactivate previous batch → audit row
    │                         └─ core/liquidity_core.py   simple interest (pure, shared with the ladder later)
    ▼
 Supabase Postgres  (RLS on, no policies: only the backend role can read/write)
   ingestion_batches · assets · liabilities · historical_log · ingestion_errors · audit_log
```

Key behaviours: a broken rule rejects **only that row**; every broken rule on a row is reported; values are **never auto-corrected**; an upload is **all-or-nothing**; re-uploading the same file never duplicates active data.

## Quick start

### 1. Supabase setup (about 10 minutes)

1. **Create the project.** supabase.com → *New project* → name `liquidplus`, pick the region closest to you, set a strong **database password** (save it), create. Wait ~2 minutes for provisioning.
2. **Copy the connection string.** Click **Connect** in the top bar → choose **Session pooler** → copy the URI. It looks like
   `postgresql://postgres.<project-ref>:[YOUR-PASSWORD]@aws-0-<region>.pooler.supabase.com:5432/postgres`.
   Replace `[YOUR-PASSWORD]` (percent-encode special characters such as `@`, `#`, `/`) and append `?sslmode=require`.
   *Why the session pooler:* the Direct connection is IPv6-only unless you buy the IPv4 add-on, so it fails on many home, college and venue networks. The Transaction pooler (port 6543) also works; the app already disables prepared statements for it.
3. **Configure the backend.**
   ```bash
   cp backend/.env.example backend/.env      # then edit DATABASE_URL (and SUPABASE_URL, see step 5)
   ```
4. **Create the tables.**
   ```bash
   cd backend && python -m app.db_migrate    # applies backend/migrations/*.sql; safe to re-run
   ```
   Check *Table Editor*: `ingestion_batches`, `assets`, `liabilities`, `historical_log`, `ingestion_errors`, `audit_log` exist and show **RLS enabled** with no policies (intended: the Supabase public API cannot read them; only the backend can).
5. **Auth (users and roles).**
   - *Authentication → Users → Add user* → create e.g. `support@liquidplus.dev` and one user per location (tick *Auto Confirm User*).
   - Roles live in **`app_metadata`** (users cannot edit it; do **not** use `user_metadata`). In the *SQL Editor*:
     ```sql
     update auth.users set raw_app_meta_data = raw_app_meta_data || '{"role":"support"}'::jsonb
      where email = 'support@liquidplus.dev';
     update auth.users set raw_app_meta_data = raw_app_meta_data || '{"role":"standard","location":"Singapore"}'::jsonb
      where email = 'singapore@liquidplus.dev';
     ```
     The new claims appear in the access token after the user's next login.
   - Token verification: put `SUPABASE_URL=https://<project-ref>.supabase.co` in `backend/.env`. New projects sign tokens with asymmetric keys and the backend fetches them from `<SUPABASE_URL>/auth/v1/.well-known/jwks.json`. If the JWKS endpoint returns no keys, your project is still on the legacy HS256 secret: either migrate to signing keys in the dashboard or set `JWT_SECRET` in `backend/.env`.
   - Only **Support** users can upload. Any logged-in user can read.
   - The login screen belongs to the auth module; until it is merged, the UI reads a token from `sessionStorage["liquidplus.token"]` (or call `setTokenProvider()` from `src/api/client.ts`). For local development only you can set `DEV_AUTH_BYPASS=true`.
6. **Verify.** Start the API (below) and open `http://localhost:8000/health/db`, it must return `{"status":"ok","database":"reachable"}`.

Notes: the free plan pauses a project after a week of inactivity (open it before a demo); you do **not** need the `service_role` key anywhere in this feature, so never put it in the frontend.

> Honesty note: the migration, API and UI were tested end to end against a local PostgreSQL 16 using the same SQL. The dashboard click-paths above and the `auth.users` SQL follow Supabase's documentation and long-standing behaviour but were not executed against a live Supabase project here, so confirm step 5 once on yours.

### 2. Run without Docker

```bash
# backend (Python 3.12)
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.db_migrate
uvicorn app.main:app --reload --port 8000

# frontend (Node 20+), in a second terminal
cd frontend
cp .env.example .env            # VITE_API_URL=http://localhost:8000
npm ci
npm run dev                     # http://localhost:5173/upload
```

### 3. Run with Docker

```bash
cp backend/.env.example backend/.env && cp .env.example .env     # edit backend/.env
docker compose up --build        # API http://localhost:8000 · web http://localhost:5173
```

**No Supabase?** `docker compose --profile localdb up --build` starts `postgres:16` (same migration applied on first start). Set `DATABASE_URL=postgresql://postgres:postgres@localdb:5432/liquidplus` in `backend/.env`.

### 4. Run and test against Supabase

```bash
# 0. one time: fill backend/.env (DATABASE_URL, SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_ROLE_KEY, SEED_USER_PASSWORD)
cd backend && pip install -r requirements.txt
python -m scripts.setup_supabase all         # connect, create tables, create the 6 users + roles

# 1. start the API (terminal 1)
uvicorn app.main:app --port 8000
curl localhost:8000/health/db                # {"status":"ok","database":"reachable",...}

# 2. get tokens for a Support user and a standard user (terminal 2)
export LIQUIDPLUS_TOKEN=$(python -m scripts.get_token support@liquidplus.dev)
export LIQUIDPLUS_STANDARD_TOKEN=$(python -m scripts.get_token singapore@liquidplus.dev)

# 3. automated end-to-end check (uploads the 3 sample files, 13 checks, expects 13/13)
python -m scripts.smoke_test

# 4. the UI (terminal 3)
cd ../frontend && cp .env.example .env && npm ci && npm run dev     # http://localhost:5173/upload
#    open the browser console on that page and run:
#    sessionStorage.setItem("liquidplus.token", "<paste $LIQUIDPLUS_TOKEN>")   then reload
#    drop the three files from backend/tests/data/ (named like "asset_book - 10102026.xlsx")
```

Expected result on the sample files:

| Book | Read | Stored | Rejected | Warnings |
|---|---:|---:|---:|---:|
| Asset | 755 | 753 | 2 | 0 |
| Liability | 1,104 | 1,103 | 1 | 0 |
| Historical | 16,186 | 16,179 | 7 | 1 |

-> **18,035 rows stored as the 2026-10-10 snapshot**, 11 error-report lines (10 rejects + 1 warning). Tokens last about an hour; run `get_token` again when they expire. Note that the smoke test replaces the active 2026-10-10 snapshot.

## API

Errors are always `{"error": {"code", "message", "details"}}`. Every response carries `x-request-id`; a 500 quotes it as the correlation id.

| Method & path | Who | Purpose |
|---|---|---|
| `POST /api/ingest/{asset\|liability\|historical}` | Support | multipart `file` (+ optional `as_of_date`). 201 → `{batch_id, book, as_of_date, filename, rows_read, rows_valid, rows_rejected, warnings, duration_ms, replaced_batch_id, errors_preview}` |
| `GET /api/ingest/summary?as_of_date=` | any user | latest **active** batch per book; `ready` = all three books for the same date |
| `GET /api/ingest/batches?limit=20` | any user | upload history |
| `GET /api/ingest/errors?batch_id=&book=&severity=&rule_code=&page=&page_size=` | any user | paginated issues (default scope: active batches) |
| `GET /api/ingest/errors.csv` (`book`, `severity`, `rule_code`, optional `batch_ids`) | any user | the Error report table as CSV: same columns (File, Row, Transaction, Field, Value found, Rule broken, Message, Severity), filters and order, all pages |
| `GET /health`, `GET /health/db` | public | liveness, and `SELECT 1` (503 if the DB is down) |

File-level failures return **422** and store nothing: `FILE_TYPE_UNSUPPORTED`, `FILE_EMPTY`, `FILE_UNREADABLE`, `FILE_MISSING_COLUMNS`, `FILE_WRONG_BOOK`, `AS_OF_DATE_INVALID`; `FILE_TOO_LARGE` is **413**; `401` no/invalid token; `403` role not allowed.

## Validation rules

All fields are mandatory and non-blank (except `Actual_Closure_Date`). Rule codes appear in the error report.

| Field(s) | Rule | Code |
|---|---|---|
| Transaction_ID | asset `AST-<n>`, liability `LIA-<n>`, historical `AST-<n>` or `LIA-<n>`; unique in file (first kept) | `ID_PATTERN`, `ID_DUPLICATE` |
| Entity | one of IBC HongKong, IBC USA, IBC UK, IBC Singapore, IBC SA | `ENTITY_INVALID` |
| Location | one of Hong Kong, New York, London, Singapore, Paris | `LOCATION_INVALID` |
| Entity ↔ Location | HongKong→Hong Kong, USA→New York, UK→London, Singapore→Singapore, SA→Paris (only checked when both are valid) | `ENTITY_LOCATION_MISMATCH` |
| Customer_ID | `CUS-<n>` | `CUSTOMER_ID_PATTERN` |
| Loan_Amount / Amount | number > 0 | `AMOUNT_INVALID` |
| Currency | USD, EUR, GBP, SGD, HKD (exact; `US`, `EURO` rejected) | `CURRENCY_INVALID` |
| Transaction_Date, Maturity_Date, Contractual_Maturity_Date, Actual_Closure_Date | real date, `YYYY-MM-DD` (Excel date cells accepted) | `DATE_FORMAT` |
| Maturity_Date (asset, liability) | ≥ Transaction_Date | `MATURITY_BEFORE_TXN` |
| Interest_Rate_Pct / Market_Interest_Rate_Pct | number 0–100 | `RATE_INVALID` / `MARKET_RATE_INVALID` |
| Loan_Type / Product_Type | `Loan-Type-A..E` (asset) · `Product-A..E` (liability) · either family (historical) | `PRODUCT_INVALID` |
| Customer_Segment | Retail, Corporate, SME, Institutional | `SEGMENT_INVALID` |
| Client_Rating | integer 1–5 | `RATING_INVALID` |
| Type (historical) | Asset / Liability, case-insensitive (stored Title case) | `TYPE_INVALID` |
| Whole row | no fully duplicate rows (first kept) | `ROW_DUPLICATE` |
| blank mandatory cell | | `REQUIRED_BLANK` |

Historical **warnings** (row is still stored): `HIST_MATURITY_BEFORE_TXN`, `HIST_CLOSURE_BEFORE_TXN`, `TYPE_ID_PREFIX_MISMATCH`. Any file: `EXTRA_COLUMN` (unknown column ignored).

Derived at ingest (asset, liability): `tenor_days`, `interest_amount = amount × rate/100 × days/365`, `cash_at_maturity = amount + interest`. Example: EUR 1,000,000 at 5%, 2026-06-20 → 2026-12-20 = 183 days, interest 25,068.49, cash 1,025,068.49. Historical: `is_early_exit` = NULL if still open, else `closure < contractual maturity`.

## Assumptions

1. **Interest:** simple interest, ACT/365 on calendar days, paid at maturity, rounded half-up to 2 dp. Amounts are quantised to 2 dp and rates to 4 dp on read. No FX conversion happens here; amounts are never summed across currencies.
2. **Duplicates:** the first occurrence wins. A later row identical to an earlier one → `ROW_DUPLICATE`; same Transaction_ID with different data → `ID_DUPLICATE` naming the first row. "First" is by position even if that first row is itself rejected for another reason.
3. **No auto-correction:** only surrounding whitespace is trimmed and `Type` is case-insensitive. `US`, `EURO`, `HBC HK`, `Deposit`, `32/13/2026` are rejected, not guessed.
4. **Support-only upload** (configurable via `UPLOAD_ROLES`). Read endpoints need any valid login and are **not location-scoped**: they expose transaction ids and rejected values, not balances. Scope them by location if that is a concern; the ladder module must enforce location filtering for balances.
5. **Snapshots:** the calculation date is the form value, else `DDMMYYYY` in the file name, else today. Uploading a book again for the same date replaces it: the old batch becomes inactive (rows kept for traceability), other dates stay active side by side. Concurrent uploads of the same book and date are serialised with a database advisory lock. The ladder link unlocks only when all three books are active for the **same** date.
6. **Historical sheet:** the use case has no maturity ≥ transaction rule for it, so such rows are stored with a warning (the sample's `AST-007027` has maturity before transaction: it is rejected from the asset book but kept, with a warning, in the historical log). Each historical upload is a full snapshot for its date, not a delta.
7. **Already-matured contracts** are stored as given; excluding them from the ladder is the ladder's decision.
8. **Files:** headers must match exactly (order irrelevant, extra columns ignored with a warning); only the first sheet of an `.xlsx` is read; Excel date cells with a time part keep the date; booleans and numeric dates are invalid; CSV must be UTF-8 (BOM tolerated). Limit `MAX_UPLOAD_MB` (default 25).
9. **File-level failures** store no data but do write an `INGEST_REJECTED` audit row.
10. **CSV report** neutralises spreadsheet formula injection (a cell starting with `=`, `+`, `@` or `-x` is prefixed with `'`).
11. **UI:** the mockup PNG was not available, so the page follows the written tokens and component list; layout details may differ from the mockup. Upload starts as soon as a file is chosen and a valid date exists.
12. A "Warnings" status chip means the upload completed but some rows were rejected or flagged; "Completed" means everything was stored cleanly.

## Tests

```bash
# backend: 118 unit tests (no DB) + 53 integration tests (real files, real Postgres)
cd backend && pip install -r requirements-dev.txt
export TEST_DATABASE_URL=postgresql://user:pass@localhost:5432/liquid_test   # THROW-AWAY database: it is wiped
pytest                       # without TEST_DATABASE_URL the integration tests are skipped

# frontend: 60 tests
cd frontend && npm test && npm run build
```

The integration suite asserts the golden numbers above, idempotency (same file twice → one active batch, no duplicates), atomicity (a failure mid-insert leaves nothing and keeps the previous batch), concurrent uploads, auth (403 for non-support, 401 for bad/forged tokens, 413 for oversize, 422 wrong book), CSV injection, RLS on all tables, and performance (16,186 rows in ~3 s, 50,000 rows in ~3.5 s).

## Layout

```
backend/app/{main,config,db,deps,logging_setup,db_migrate}.py
backend/app/core/liquidity_core.py      interest maths (pure)
backend/app/services/{parsing,validation,ingestion,queries,errors}.py
backend/app/routers/{ingest,health}.py
backend/migrations/001_init.sql
backend/tests/{unit,integration,data}
frontend/src/{pages,components,api,lib,styles}
docker-compose.yml  ·  .env.example  ·  backend/.env.example  ·  frontend/.env.example
```

## Troubleshooting

- `/health/db` is 503 → check `DATABASE_URL` (password encoding, `sslmode=require`), use the **Session pooler** string, and make sure the project is not paused.
- Every data call returns **503 `DATABASE_UNAVAILABLE`** after ~15 s → the API cannot open a database connection (it gives up after `DB_CONNECT_TIMEOUT` seconds per pooler address instead of hanging). Most often the network blocks ports 5432/6543: run `Test-NetConnection <pooler-host> -Port 5432` (Windows) or `nc -vz <pooler-host> 5432`, and switch to another network or a phone hotspot if it fails.
- 401 on every call → no token: sign in, or set `DEV_AUTH_BYPASS=true` locally. 403 on upload → the user's `app_metadata.role` is not `support`.
- Browser shows "Cannot reach the server" → `VITE_API_URL` is wrong, or the API's `CORS_ORIGINS` does not include the page's origin.


## Deterministic gap ladder (`/ladder`)

Reads the **active asset + liability snapshot** from Postgres and builds the maturity-gap ladder on request (no cache, no randomness).

- `GET /api/ladder` – buckets 0-1M, 1-3M, 3-12M, 1-5Y, >5Y; net gap = assets − liabilities per currency x location + per-currency roll-up. Params: `as_of_date`, `calc_date` (default = snapshot date), `past_due_policy` (`exclude` | `bucket_0_1m`).
- `GET /api/ladder/transactions` – drill-down behind any cell (filters, sort, paging; `bucket=past_due`).
- `GET /api/ladder/explain/{transaction_id}` – six-step working for one transaction.
- Maths: `backend/app/core/ladder.py` (pure). Interest is the value stored at ingest (`liquidity_core`, ACT/365 half-up). Buckets are calendar months, upper edge inclusive, due-today = 0-1M, past due excluded + listed by default.
- **Location scoping:** Support sees all; a standard user gets only their JWT `location` (filtered in SQL, roll-ups included); no location -> 403 `LOCATION_NOT_ASSIGNED`.
- Needs asset + liability for the same date (historical is not required): otherwise 409 `SNAPSHOT_NOT_READY`.
- Tests: `tests/unit/test_ladder_core.py`, `tests/integration/test_ladder_api.py` (every currency x location x bucket cell checked against `tests/data/golden_deterministic_ladder.json`).

## AI gap ladder (`/ai`): Compare · AI ladder · Why it changed · Drill-down

Predicts which live loans will be pre-closed and which deposits will be withdrawn early, moves each one to an
**adjusted maturity** (cash amount unchanged, only the date moves) and compares the result with the deterministic ladder.

**Model** (`backend/app/core/behavioral.py`, numpy only, no ML library)
1. *Early-exit probability*: logistic regression **written from scratch** (Newton-Raphson / IRLS, L2 = 1) trained on
   **every closed contract** of the historical log (14,325 on the sample; the 1,854 still-open contracts have no
   outcome yet and are scored, not trained on). Main driver is the **rate incentive**: loan rate − market rate for
   loans, market rate − deposit rate for deposits; plus segment, client rating, product, location, tenor, amount.
   Validated against scikit-learn: identical coefficients (to 1e-7) and AUC.
2. *Timing*: early exits happen at 40–90 % of a contract's life (median ~66 %), taken from history.
3. *Conditioning on today*: `p_now = p·(1−F(e)) / (1−p·F(e))`, e = share of life already passed.
4. *Adjusted maturity* = contractual − p_now × (contractual − expected early-exit date). Amounts never change, so the
   total net gap is identical in both views and every difference is timing.
5. *Range*: 1,000 Monte Carlo runs (fixed seed per snapshot) → P5 / P50 / P95 net gap per bucket.
6. *Recommendations* per bucket from the AI gap and its simulated bad case ("Raise short-term funding – EUR 49.0M").
7. *Market-rate scenario*: re-scores every contract with all market rates shifted by −2…+2 pp.

Holdout (newest 20 % by transaction date): AUC 0.744, Brier 0.234 (0.276 flat), accuracy 62.5 % / F1 0.48 at a 50 %
cut-off, F1 0.71 at the best cut-off (21 %). Shown with calibration on the model card ("About the model").

**Views**
- *Compare*: KPIs, cumulative net gap by month (24 months, divergence shaded), AI − deterministic per month
  (click a bar), bucket comparison, P5–P95 range, recommended actions. Filters: currency, location, side, segment.
- *AI ladder*: assets / liabilities / net gap per bucket at AI-adjusted maturities (same chart as the deterministic ladder).
- *Why it changed*: per-bucket explanation, what drives the model, who moved, market-rate scenario slider, model card.
- *Drill-down*: filters (AI bucket, currency, side, segment, P(early), spread, moved only), KPIs, contracts with reason
  chips, per-contract "why it moved" panel (log-odds contributions), CSV export of every prediction.

**API** (same auth, location scoping, buckets and `calc_date` / `past_due_policy` as `/api/ladder`)
- `GET /api/behavioral/compare` · `/ladder` · `/why?market_shift_pp=` · `/transactions` · `/explain/{id}` · `/model`
- `GET /api/behavioral/transactions.csv` – predictions for every live contract (filters optional)
- `GET /api/behavioral/history-predictions.csv` – a prediction for every historical row (closed and open)

Needs the historical log uploaded (409 `MODEL_NOT_READY` otherwise).

**Caching / speed**
- *API*: the live book, the history, the trained model and every computed view are kept in memory per snapshot
  (keyed by batch ids); the "which batches are active" lookup is refreshed every 60 s and immediately after an upload
  through this API. Concurrent requests for the same snapshot compute it once. The default views are pre-computed in
  the background at startup and after each upload, so the first visit is fast.
- *Browser*: responses are kept for 10 minutes (per user and parameters) and cleared after an upload; the four tabs stay
  mounted once opened; filters, selected bucket/contract and the scenario survive switching tabs and pages.
  Opening Compare pre-loads the other tabs in the background. New dependency: `numpy`. Tests: `tests/unit/test_behavioral_core.py`, `tests/integration/test_behavioral_api.py`,
`frontend/src/pages/Behavioral.test.tsx`.
