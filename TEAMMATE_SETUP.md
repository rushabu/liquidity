# Liquid+ Data Ingestion: Teammate Setup (shared Supabase)

This guide gets the app running on **your own laptop**, using the **same Supabase project** as the rest of the team.
The database, tables and test users already exist, so you do **not** create a Supabase project and you do **not** run `setup_supabase`.

Time needed: about 15 minutes.

```
Browser (localhost:5173)  ->  FastAPI on your laptop (localhost:8000)  ->  Shared Supabase Postgres
```

---

## 0. What you need before you start

**Tools installed on your laptop**

| Tool | Version | Check with |
|---|---|---|
| Git | any recent | `git --version` |
| Python | 3.11 or newer | `python --version` (on Mac/Linux you may need `python3`) |
| Node.js | 20 or newer | `node --version` |

**Access**

1. Access to the GitHub repo: https://github.com/rushabu/liquid-innoversite (ask Rushabh to add you as a collaborator if it is private).
2. Four values from Rushabh, sent **privately** (direct message or password manager, never in a group chat, never in git):

| Value | Used for |
|---|---|
| `DATABASE_URL` | the API's connection to the database (contains the DB password) |
| `SUPABASE_URL` | looks like `https://<project-ref>.supabase.co` |
| `SUPABASE_ANON_KEY` | the publishable key (`sb_publishable_...`), used to get a login token |
| `SEED_USER_PASSWORD` | the password of the test users, used to get a login token |

You do **not** need the `SUPABASE_SERVICE_ROLE_KEY` (the secret key). Do not ask for it. It is only for one-time project setup.

---

## 1. Get the code

```
git clone https://github.com/rushabu/liquid-innoversite.git
cd liquid-innoversite
```

(If the project folder inside the repo has a different name, such as `liquidplus`, `cd` into the folder that contains the `backend` and `frontend` folders.)

---

## 2. Check that your network can reach Supabase

Some Wi-Fi networks (college, office, event) block the database port. Test it now:

**Windows (PowerShell)**
```
Test-NetConnection aws-0-ap-northeast-1.pooler.supabase.com -Port 5432
```
Look for `TcpTestSucceeded : True`.

**Mac / Linux**
```
nc -vz aws-0-ap-northeast-1.pooler.supabase.com 5432
```
Look for "succeeded" or "open".

If it fails: switch to a phone hotspot, or try port `6543` instead (then change `5432` to `6543` in `DATABASE_URL` in step 3). Do not continue until one of them works.

---

## 3. Create `backend/.env`

Go into the `backend` folder and copy the example file.

**Windows**
```
cd backend
copy .env.example .env
notepad .env
```

**Mac / Linux**
```
cd backend
cp .env.example .env
nano .env
```

Fill it in with the values Rushabh sent you. The finished file should look like this:

```
ENVIRONMENT=dev
LOG_LEVEL=INFO
CORS_ORIGINS=["http://localhost:5173"]

DATABASE_URL=<paste the DATABASE_URL from Rushabh>
SUPABASE_URL=<paste the SUPABASE_URL from Rushabh>
JWT_SECRET=
JWT_AUDIENCE=authenticated
DEV_AUTH_BYPASS=false

SUPABASE_SERVICE_ROLE_KEY=
SUPABASE_ANON_KEY=<paste the publishable key from Rushabh>
SEED_USER_PASSWORD=<paste the password from Rushabh>

MAX_UPLOAD_MB=25
```

Rules for this file:

- The file must be named exactly `.env` (not `.env.txt`, not `.env.example`). On Windows, Notepad's "Save as" dialog must have "All files" selected.
- Leave `JWT_SECRET` and `SUPABASE_SERVICE_ROLE_KEY` **empty**. Keep `DEV_AUTH_BYPASS=false`.
- No spaces around `=`, and no quotes around values.
- `DATABASE_URL` must start with `postgresql://`, contain `pooler.supabase.com`, and end with `?sslmode=require`.
- **Never** put real values into `.env.example`, and never commit `.env`. Git ignores `.env`, and GitHub blocks pushes that contain secrets.

---

## 4. Install the backend

Still in the `backend` folder:

**Windows (PowerShell)**
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

If PowerShell says "running scripts is disabled", run this once in the same window and activate again:
```
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

**Mac / Linux**
```
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Your prompt now starts with `(.venv)`. Always activate the venv before running backend commands.

> Do NOT run `python -m scripts.setup_supabase`. The shared database is already set up.

---

## 5. Start the API (Terminal 1)

```
uvicorn app.main:app --port 8000
```

Leave it running. In your browser open http://localhost:8000/health/db. You should see:

```
{"status":"ok","database":"reachable","latency_ms":...}
```

The first call can take a few seconds. `http://localhost:8000/` itself shows "Not found", which is normal. Use `/docs` to see the API pages.

---

## 6. Get a login token (Terminal 2)

Open a **second** terminal:

**Windows**
```
cd <project folder>\backend
.venv\Scripts\activate
python -m scripts.get_token support@liquidplus.dev
```

**Mac / Linux**
```
cd <project folder>/backend
source .venv/bin/activate
python -m scripts.get_token support@liquidplus.dev
```

It prints one long line starting with `eyJ`. Copy it. This is your token. It expires after about 1 hour, so repeat this step when you get "Missing bearer token" or a 401.

Which user to use:

| User | Can upload? |
|---|---|
| `support@liquidplus.dev` | **Yes** (use this one for ingestion) |
| `london@liquidplus.dev`, `hongkong@liquidplus.dev`, `newyork@liquidplus.dev`, `singapore@liquidplus.dev`, `paris@liquidplus.dev` | No (read only, uploads give 403) |

---

## 7. Start the frontend (Terminal 3)

```
cd <project folder>/frontend
```

**Windows:** `copy .env.example .env`
**Mac / Linux:** `cp .env.example .env`

Then:
```
npm ci
npm run dev
```

Open http://localhost:5173/upload.

You will see a red "Missing bearer token" banner until you do step 8.

---

## 8. Give the browser your token

On the upload page press **F12**, open the **Console** tab and run (paste your token between the quotes):

```
sessionStorage.setItem("liquidplus.token", "PASTE_YOUR_TOKEN_HERE")
```

If Chrome refuses to paste, type `allow pasting` and press Enter first. Then reload the page with **F5**. The banner should disappear.

The token is cleared when you close the tab. Repeat this step in each new tab.

---

## 9. Upload the sample files

The three sample files are in `backend/tests/data/`. Drop them on the page (or click "Choose a file"). The names must keep the date in `DDMMYYYY` form, for example:

- `asset_book - 10102026.xlsx`
- `liability_book - 10102026.xlsx`
- the historical log file

The calculation date fills in from the file name.

**Expected result**

| Book | Stored |
|---|---|
| Asset | 753 |
| Liability | 1,103 |
| Historical | 16,179 |

Total 18,035 rows stored, 11 lines in the error report (rejected rows with the rule they broke). The "Download CSV" button gives you the error report.

---

## 10. Working on a shared database (read this)

Everyone's laptop talks to the **same** database.

- Uploading a book for a date that already exists **replaces** that earlier upload (the old one is marked inactive). If you only want to experiment, upload copies of the files renamed with a different date, for example `asset_book - 11102026.xlsx`, so you do not overwrite what a teammate is using.
- **Never run `python -m scripts.reset_data` on the shared database.** It deletes everyone's uploaded data.
- Do not change the schema or the users on your own. Ask Rushabh.
- If you want a private copy to break things, create your own free Supabase project and follow the main README instead.

---

## 11. Daily routine (after the first setup)

| Terminal | Commands |
|---|---|
| 1 (API) | `cd backend` then activate the venv, then `uvicorn app.main:app --port 8000` |
| 2 (token) | `cd backend`, activate the venv, `python -m scripts.get_token support@liquidplus.dev` |
| 3 (frontend) | `cd frontend` then `npm run dev` |
| Browser | `sessionStorage.setItem("liquidplus.token", "...")`, reload |

Update the code when teammates push changes:
```
git pull
cd backend && pip install -r requirements.txt
cd ../frontend && npm ci
```

---

## 12. Troubleshooting

| Problem | Fix |
|---|---|
| `database_url Field required` | `backend/.env` is missing or misnamed, or you are not in the `backend` folder. Run `dir .env*` (Windows) or `ls -a` (Mac/Linux). |
| `connection timeout expired` | Your network blocks the database port. Use a hotspot, or switch to port 6543 in `DATABASE_URL`. |
| `password authentication failed` | The password in `DATABASE_URL` is wrong. Ask Rushabh to re-send it. Special characters must be percent-encoded (`@` is `%40`). |
| `Tenant or user not found` | The user in `DATABASE_URL` must be `postgres.<project-ref>`, not just `postgres`. |
| `get_token`: `Invalid login credentials` | `SEED_USER_PASSWORD` is wrong or outdated. Ask Rushabh. |
| `get_token`: `404` on `/auth/v1/...` | `SUPABASE_URL` is wrong. It must be `https://<project-ref>.supabase.co`. |
| "Missing bearer token" / 401 on the page | Token missing or expired. Repeat steps 6 and 8. |
| 403 on upload | You used a non-support user. Use `support@liquidplus.dev`. |
| Page shows data from the wrong date | The calculation date comes from the file name (`DDMMYYYY`). Check the box at the top right. |
| `uvicorn` not found / module not found | The venv is not active. Activate it (step 4) and run `pip install -r requirements.txt` again. |
| Port 8000 or 5173 already in use | Close the other program using it, or start on another port (`--port 8001`, and change `VITE_API_URL` in `frontend/.env`). |
| Project is unreachable for everyone | Free Supabase projects pause after inactivity. Rushabh restores it from the dashboard. |

---

## 13. Security rules

- `backend/.env` holds real secrets. It is git-ignored. Never commit it, screenshot it or paste it into chat.
- `.env.example` is committed to git, so it must only ever contain placeholders.
- The Supabase secret key (`sb_secret_...`) never goes in the frontend, in git or in a shared chat.
- If you think a secret leaked, tell Rushabh straight away so the key can be rotated in the Supabase dashboard.
