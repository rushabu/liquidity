"""Delete all ingested data. Keeps the schema, the users and the migration history."""
import psycopg
from app.config import get_settings

TABLES = ["ingestion_errors", "assets", "liabilities",
          "historical_log", "ingestion_batches", "audit_log"]

def main() -> int:
    answer = input("This deletes ALL ingested data. Type yes to continue: ")
    if answer.strip().lower() != "yes":
        print("cancelled")
        return 1
    with psycopg.connect(get_settings().database_url) as conn:
        conn.execute("TRUNCATE " + ", ".join(TABLES) + " RESTART IDENTITY CASCADE")
        for t in TABLES:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            print(f"{t}: {n} rows")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())