"""Data for the behavioural (AI-adjusted) ladder: the active historical log (training) and the active live
snapshot with the extra model inputs. Location scoping for live rows is applied in SQL, exactly like the
deterministic ladder. The model is trained on the whole historical log (all locations): only aggregated
coefficients leave this module, never another location's transactions.

Trained models and per-snapshot results are cached in memory, keyed by the batch ids they were built from,
so repeat views do not retrain or re-simulate; a new upload changes the ids and naturally invalidates them.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ..core import behavioral as bm
from ..deps import CurrentUser
from .errors import ApiError
from .ladder_queries import active_batches, allowed_location

LIVE_COLS = {
    "asset": ("assets", "loan_amount", "loan_type"),
    "liability": ("liabilities", "amount", "product_type"),
}


class _LRU:
    def __init__(self, size: int):
        self.size, self.data, self.lock = size, OrderedDict(), threading.Lock()

    def get_or(self, key, build):
        with self.lock:
            if key in self.data:
                self.data.move_to_end(key)
                return self.data[key]
        value = build()                      # built outside the lock; a duplicate build is harmless
        with self.lock:
            self.data[key] = value
            self.data.move_to_end(key)
            while len(self.data) > self.size:
                self.data.popitem(last=False)
        return value

    def clear(self):
        with self.lock:
            self.data.clear()


MODELS = _LRU(4)
RESULTS = _LRU(32)


def historical_batch(engine: Engine, as_of: date | None) -> dict:
    """The active historical batch for the snapshot date, else the newest active one."""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            select id, as_of_date, source_filename, rows_valid from ingestion_batches
             where is_active and status = 'completed' and book = 'historical'
             order by (as_of_date = cast(:d as date)) desc nulls last, as_of_date desc, created_at desc
             limit 1"""), {"d": as_of}).mappings().all()
    if not rows:
        raise ApiError(409, "MODEL_NOT_READY",
                       "The behavioural model needs the historical log. Upload it on the Data ingestion page.")
    r = rows[0]
    return {**r, "id": str(r["id"])}


def fetch_history(engine: Engine, batch_id: str) -> list[dict]:
    with engine.connect() as conn:
        res = conn.execute(text("""
            select transaction_id, lower(type) as book, transaction_date, contractual_maturity_date as maturity_date,
                   actual_closure_date, amount, interest_rate_pct, market_rate_pct, client_rating, customer_segment,
                   product_type as product, location, is_early_exit
              from historical_log where batch_id = :b order by transaction_id"""), {"b": batch_id}).mappings().all()
    return [dict(r) for r in res]


def get_model(engine: Engine, as_of: date | None) -> tuple[dict, bm.Model]:
    hb = historical_batch(engine, as_of)

    def build():
        try:
            return bm.train(fetch_history(engine, hb["id"]))
        except ValueError as e:
            raise ApiError(409, "MODEL_NOT_READY", f"The behavioural model cannot be trained: {e}") from e
    return hb, MODELS.get_or(hb["id"], build)


def fetch_live(engine: Engine, user: CurrentUser, as_of: date | None) -> tuple[date, dict, list[dict]]:
    """Active asset + liability rows with the model inputs (same scoping and order as the deterministic ladder)."""
    location = allowed_location(user)
    batches = active_batches(engine, as_of)
    snapshot = batches["asset"]["as_of_date"]
    rows: list[dict] = []
    with engine.connect() as conn:
        for book, (table, amt, product) in LIVE_COLS.items():
            res = conn.execute(text(f"""
                select transaction_id, entity, location, customer_id, {amt} as amount, currency, transaction_date,
                       maturity_date, interest_rate_pct, market_rate_pct, customer_segment, client_rating,
                       {product} as product, tenor_days, interest_amount, cash_at_maturity
                  from {table}
                 where batch_id = :b and (cast(:loc as text) is null or location = :loc)
                 order by transaction_id"""), {"b": batches[book]["id"], "loc": location}).mappings().all()
            rows.extend({**dict(r), "book": book} for r in res)
    return snapshot, batches, rows
