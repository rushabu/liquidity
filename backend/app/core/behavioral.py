"""Behavioural (AI-adjusted) model: pure functions, numpy only, no database, no HTTP.

1. **Early-exit probability** (loan pre-closure / deposit early withdrawal): a logistic regression written from
   scratch (Newton-Raphson / IRLS with a small L2 penalty) and trained on EVERY closed contract of the
   historical log. Main driver is the *rate incentive*:
       loans:    contract rate − market rate   (borrower pays above market -> refinances early)
       deposits: market rate − contract rate   (depositor earns below market -> moves the money)
   plus tenor, amount, market rate, client rating, customer segment, product and location.
2. **Timing of an early exit**: the fraction of the contract's life at which early exits happened in history.
3. **Conditioning on today**: p_now = p·(1 − F(e)) / (1 − p·F(e)), e = share of the life already passed,
   F = timing CDF; expected exit point = E[fraction | fraction > e].
4. **Adjusted maturity** (one date per transaction): contractual − p_now × (contractual − expected exit date).
   The cash amount is unchanged (principal + contractual interest); only its date moves, so the total net gap
   is identical in both views and every difference is pure timing.
5. **Monte Carlo range**: each contract exits early with p_now at a date drawn from the conditional timing
   distribution; N runs with a fixed seed -> P5 / P50 / P95 net gap per bucket.
Predictions are produced for every live contract and every historical row (open contracts included).
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np

from . import ladder as det

NUMERIC = ("incentive_pp", "tenor_years", "log_amount", "market_rate_pct", "client_rating")
CATEGORICAL = ("customer_segment", "product", "location")
NUMERIC_LABEL = {
    "incentive_pp": "Rate vs market",
    "tenor_years": "Tenor",
    "log_amount": "Amount",
    "market_rate_pct": "Market rate",
    "client_rating": "Client rating",
}
CATEGORICAL_LABEL = {"customer_segment": "Customer segment", "product": "Product", "location": "Location"}
MIN_TRAINING_ROWS = 50
DEFAULT_SIMULATIONS = 1000
L2 = 1.0                                  # ridge penalty on the weights (not the intercept)
MODEL_NAME = "early-exit-logit-v2"


# ------------------------------------------------------------------------------------------ metrics
def auc(y: np.ndarray, p: np.ndarray) -> float:
    """Area under the ROC curve = probability a random early exit scores above a random stay (ties = ½)."""
    order = np.argsort(p, kind="mergesort")
    ranks = np.empty(len(p))
    sp = p[order]
    i = 0
    while i < len(sp):                    # average ranks over ties
        j = i
        while j + 1 < len(sp) and sp[j + 1] == sp[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    n1 = int(y.sum())
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def classification(y: np.ndarray, p: np.ndarray, threshold: float) -> dict:
    pred = p >= threshold
    tp = int((pred & (y == 1)).sum())
    fp = int((pred & (y == 0)).sum())
    fn = int((~pred & (y == 1)).sum())
    tn = int((~pred & (y == 0)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"threshold": round(float(threshold), 2), "accuracy": round((tp + tn) / len(y), 3), "precision": round(precision, 3),
            "recall": round(recall, 3), "f1": round(f1, 3), "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


# --------------------------------------------------------------------------------------------- features
def raw_features(row: dict, market_shift_pp: float = 0.0) -> dict:
    """Model inputs for one contract (historical or live). `book` is 'asset' or 'liability'.
    `market_shift_pp` simulates a parallel move of the market rate (scenario analysis)."""
    tenor_days = max(1, (row["maturity_date"] - row["transaction_date"]).days)
    rate, market = float(row["interest_rate_pct"]), float(row["market_rate_pct"]) + market_shift_pp
    spread = rate - market
    return {
        "incentive_pp": spread if row["book"] == "asset" else -spread,
        "spread_pp": spread,
        "tenor_years": tenor_days / 365.0,
        "log_amount": math.log(max(float(row["amount"]), 1.0)),
        "market_rate_pct": market,
        "client_rating": float(row["client_rating"]),
        "customer_segment": row["customer_segment"],
        "product": row["product"],
        "location": row["location"],
    }


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = L2, max_iter: int = 50, tol: float = 1e-8) -> tuple[float, np.ndarray, int]:
    """Logistic regression from scratch: Newton-Raphson (iteratively re-weighted least squares).
    Minimises  Σ logloss + l2/2 · ||w||²  (intercept not penalised). Returns (intercept, weights, iterations)."""
    n, k = X.shape
    A = np.column_stack([np.ones(n), X])
    beta = np.zeros(k + 1)
    beta[0] = math.log(max(y.mean(), 1e-6) / max(1 - y.mean(), 1e-6))
    pen = np.full(k + 1, l2)
    pen[0] = 0.0
    it = 0
    for it in range(1, max_iter + 1):
        p = _sigmoid(A @ beta)
        grad = A.T @ (p - y) + pen * beta
        w = p * (1 - p)
        H = (A * w[:, None]).T @ A + np.diag(pen) + 1e-9 * np.eye(k + 1)
        step = np.linalg.solve(H, grad)
        beta -= step
        if np.max(np.abs(step)) < tol:
            break
    return float(beta[0]), beta[1:], it


@dataclass
class Model:
    num_mean: dict[str, float]
    num_std: dict[str, float]
    levels: dict[str, list[str]]
    level_mean: dict[str, dict[str, float]]
    coef_num: dict[str, float]
    coef_cat: dict[str, dict[str, float]]
    intercept: float                       # log-odds of a contract with every input at the training average
    base_rate: float                       # observed early-exit share in the training data
    exit_fractions: np.ndarray             # sorted fractions of life at which early exits happened
    metrics: dict = field(default_factory=dict)
    n_train: int = 0
    n_early: int = 0
    iterations: int = 0
    name: str = MODEL_NAME

    # -- design matrix: numeric inputs standardised, categories one-hot and centred (no level dropped) --
    def matrix(self, feats: Sequence[dict]) -> np.ndarray:
        cols: list[np.ndarray] = []
        for n in NUMERIC:
            v = np.array([f[n] for f in feats], dtype=float)
            cols.append((v - self.num_mean[n]) / self.num_std[n])
        for c in CATEGORICAL:
            for lvl in self.levels[c]:
                v = np.array([1.0 if f[c] == lvl else 0.0 for f in feats])
                cols.append(v - self.level_mean[c][lvl])
        return np.column_stack(cols) if cols else np.zeros((len(feats), 0))

    def coef_vector(self) -> np.ndarray:
        return np.array([self.coef_num[n] for n in NUMERIC]
                        + [self.coef_cat[c][lvl] for c in CATEGORICAL for lvl in self.levels[c]])

    def predict(self, feats: Sequence[dict]) -> np.ndarray:
        if not feats:
            return np.zeros(0)
        return _sigmoid(self.intercept + self.matrix(feats) @ self.coef_vector())

    # -- timing ------------------------------------------------------------------------------------
    def timing_cdf(self, e: np.ndarray) -> np.ndarray:
        s = self.exit_fractions
        return np.searchsorted(s, e, side="right") / len(s)

    def tail_mean(self, e: np.ndarray) -> np.ndarray:
        """E[fraction | fraction > e]; NaN where no historical exit happened that late."""
        s = self.exit_fractions
        k = np.searchsorted(s, e, side="right")
        csum = np.concatenate([[0.0], np.cumsum(s)])
        n_tail = len(s) - k
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(n_tail > 0, (csum[-1] - csum[k]) / np.maximum(n_tail, 1), np.nan)


def _fit(feats: list[dict], y: np.ndarray) -> Model:
    num_mean = {n: float(np.mean([f[n] for f in feats])) for n in NUMERIC}
    num_std = {n: float(np.std([f[n] for f in feats])) or 1.0 for n in NUMERIC}
    levels = {c: sorted({f[c] for f in feats}) for c in CATEGORICAL}
    level_mean = {c: {lvl: float(np.mean([f[c] == lvl for f in feats])) for lvl in levels[c]} for c in CATEGORICAL}
    m = Model(num_mean, num_std, levels, level_mean, {}, {}, 0.0, float(y.mean()), np.array([0.65]))
    b0, w, it = fit_logistic(m.matrix(feats), y.astype(float))
    m.coef_num = {n: float(w[i]) for i, n in enumerate(NUMERIC)}
    i = len(NUMERIC)
    for c in CATEGORICAL:
        m.coef_cat[c] = {}
        for lvl in levels[c]:
            m.coef_cat[c][lvl] = float(w[i])
            i += 1
    m.intercept, m.iterations = b0, it
    return m


def _life_fraction(r: dict) -> float:
    return (r["actual_closure_date"] - r["transaction_date"]).days / (r["maturity_date"] - r["transaction_date"]).days


def labelled(history: Iterable[dict]) -> list[dict]:
    """Every historical contract whose outcome is recorded (closed, early or at maturity), oldest first."""
    rows = [r for r in history if r.get("is_early_exit") is not None and r["maturity_date"] > r["transaction_date"]]
    rows.sort(key=lambda r: (r["transaction_date"], r["transaction_id"]))
    return rows


def train(history: Iterable[dict], holdout_share: float = 0.2) -> Model:
    """Train on ALL closed historical contracts. Metrics come from a time-ordered holdout (the newest
    `holdout_share` by transaction date, never seen in that trial fit) before the final fit on everything."""
    history = list(history)
    closed = labelled(history)
    if len(closed) < MIN_TRAINING_ROWS:
        raise ValueError(f"need at least {MIN_TRAINING_ROWS} closed historical contracts, got {len(closed)}")
    feats = [raw_features(r) for r in closed]
    y = np.array([1 if r["is_early_exit"] else 0 for r in closed])
    if y.min() == y.max():
        raise ValueError("the historical log has no variation in early exits (all or none), so nothing can be learned")

    metrics: dict = {}
    cut = int(len(closed) * (1 - holdout_share))
    if 0 < cut < len(closed) and 0 < y[:cut].sum() < cut and 0 < y[cut:].sum() < len(closed) - cut:
        trial = _fit(feats[:cut], y[:cut])
        p, yt = trial.predict(feats[cut:]), y[cut:]
        best = max((classification(yt, p, t) for t in np.round(np.arange(0.10, 0.81, 0.01), 2)), key=lambda c: c["f1"])
        groups = np.array_split(np.argsort(p), 5)
        metrics = {
            "holdout": f"newest {holdout_share:.0%} of contracts by transaction date",
            "train_rows": int(cut), "test_rows": int(len(yt)), "test_early_rate": round(float(yt.mean()), 3),
            "auc": round(auc(yt, p), 3),
            "brier": round(brier(yt, p), 3),
            "brier_baseline": round(brier(yt, np.full(len(yt), y[:cut].mean())), 3),
            "at_50": classification(yt, p, 0.5),
            "best_f1": best,
            "calibration": [{"predicted": round(float(p[g].mean()), 3), "observed": round(float(yt[g].mean()), 3),
                             "n": int(len(g))} for g in groups if len(g)],
        }

    model = _fit(feats, y)
    fr = [_life_fraction(r) for r in closed if r["is_early_exit"] and r.get("actual_closure_date")]
    model.exit_fractions = np.sort(np.clip(np.array(fr, dtype=float), 0.0, 1.0)) if fr else np.array([0.65])
    in_sample = model.predict(feats)
    metrics["in_sample_auc"] = round(auc(y, in_sample), 3)
    metrics["history_rows"] = len(history)
    metrics["open_rows"] = sum(1 for r in history if r.get("is_early_exit") is None)
    model.metrics = metrics
    model.n_train, model.n_early = len(closed), int(y.sum())
    return model


# ------------------------------------------------------------------------------------------ prediction
def predict_live(model: Model, rows: Sequence[dict], calc_date: date, market_shift_pp: float = 0.0) -> list[dict]:
    """One prediction per live contract (same order as `rows`). Past-due contracts are left unchanged."""
    if not rows:
        return []
    feats = [raw_features(r, market_shift_pp) for r in rows]
    p_raw = model.predict(feats)
    life = np.array([max(1, (r["maturity_date"] - r["transaction_date"]).days) for r in rows], dtype=float)
    elapsed = np.clip(np.array([(calc_date - r["transaction_date"]).days for r in rows], dtype=float) / life, 0.0, 1.0)
    F = model.timing_cdf(elapsed)
    tail = model.tail_mean(elapsed)
    p_now = np.where(np.isnan(tail), 0.0, p_raw * (1 - F) / np.maximum(1e-12, 1 - p_raw * F))
    out: list[dict] = []
    for i, r in enumerate(rows):
        past_due = r["maturity_date"] < calc_date
        pn = 0.0 if past_due else float(p_now[i])
        if past_due or np.isnan(tail[i]) or pn <= 0:
            exit_date, adjusted = None, r["maturity_date"]
        else:
            exit_date = r["transaction_date"] + timedelta(days=int(round(tail[i] * life[i])))
            exit_date = min(max(exit_date, calc_date), r["maturity_date"])
            shift = (r["maturity_date"] - exit_date).days
            adjusted = r["maturity_date"] - timedelta(days=int(round(pn * shift)))
        out.append({
            "transaction_id": r["transaction_id"], "book": r["book"],
            "p_early_base": round(float(p_raw[i]), 4), "p_early": round(pn, 4),
            "elapsed_fraction": round(float(elapsed[i]), 4),
            "expected_exit_date": exit_date, "contractual_maturity_date": r["maturity_date"],
            "adjusted_maturity_date": adjusted, "shift_days": (r["maturity_date"] - adjusted).days,
            "features": feats[i],
        })
    return out


def predict_history(model: Model, history: Sequence[dict]) -> list[dict]:
    """A prediction for every historical row, open contracts included (their outcome is not yet known)."""
    feats = [raw_features(r) for r in history]
    p = model.predict(feats) if feats else np.zeros(0)
    return [{"transaction_id": r["transaction_id"], "book": r["book"], "customer_segment": r["customer_segment"],
             "location": r["location"], "transaction_date": r["transaction_date"],
             "contractual_maturity_date": r["maturity_date"], "actual_closure_date": r.get("actual_closure_date"),
             "status": "open" if r.get("is_early_exit") is None else "closed",
             "actual_early_exit": r.get("is_early_exit"), "p_early": round(float(p[i]), 4),
             "spread_pp": round(feats[i]["spread_pp"], 4)} for i, r in enumerate(history)]


def adjusted_rows(rows: Sequence[dict], preds: Sequence[dict]) -> list[dict]:
    """Live rows re-dated to their adjusted maturity; amounts unchanged (same schema as the ladder rows)."""
    by_id = {p["transaction_id"]: p for p in preds}
    out = []
    for r in rows:
        p = by_id[r["transaction_id"]]
        out.append({**r, "contractual_maturity_date": r["maturity_date"], "maturity_date": p["adjusted_maturity_date"],
                    "p_early": p["p_early"], "shift_days": p["shift_days"], "spread_pp": p["features"]["spread_pp"]})
    return out


# ------------------------------------------------------------------------------------------- rationale
def _fmt_amt(v: float) -> str:
    a = abs(v)
    return f"{a / 1e9:.2f}B" if a >= 1e9 else f"{a / 1e6:.1f}M" if a >= 1e6 else f"{a / 1e3:.1f}K" if a >= 1e3 else f"{a:.0f}"


def _describe(name: str, f: dict, book: str, currency: str) -> str:
    if name == "incentive_pp":
        s = f["spread_pp"]
        return f"Rate spread {'+' if s >= 0 else '−'}{abs(s):.1f} pp"
    if name == "tenor_years":
        return f"Tenor {f['tenor_years']:.1f} years"
    if name == "log_amount":
        return f"Amount {currency} {_fmt_amt(math.exp(f['log_amount']))}"
    if name == "market_rate_pct":
        return f"Market rate {f['market_rate_pct']:.2f}%"
    if name == "client_rating":
        return f"Client rating {int(f['client_rating'])}"
    return f"{CATEGORICAL_LABEL[name]}: {f[name]}"


def _contributions(model: Model, f: dict) -> tuple[float, list[tuple[str, float]]]:
    x = model.matrix([f])[0]
    contrib = x * model.coef_vector()
    out, i = [], 0
    for n in NUMERIC:
        out.append((n, float(contrib[i])))
        i += 1
    for c in CATEGORICAL:
        k = len(model.levels[c])
        out.append((c, float(contrib[i:i + k].sum())))
        i += k
    return model.intercept + float(contrib.sum()), out


def rationale(model: Model, row: dict, pred: dict) -> dict:
    """Which inputs pushed this contract's early-exit probability up or down. Exact for a logistic model:
    log-odds = intercept + Σ contributions; effect_pp = probability change if that input were average."""
    f = pred["features"]
    logit, drivers = _contributions(model, f)
    p = float(_sigmoid(logit))
    items = []
    for name, c in drivers:
        items.append({
            "feature": name,
            "label": NUMERIC_LABEL.get(name) or CATEGORICAL_LABEL[name],
            "value": _describe(name, f, row["book"], row["currency"]),
            "log_odds": round(c, 4),
            "effect_pp": round((p - float(_sigmoid(logit - c))) * 100, 1),
            "direction": "raises" if c > 0 else "lowers" if c < 0 else "neutral",
        })
    items.sort(key=lambda d: abs(d["log_odds"]), reverse=True)
    avg = float(_sigmoid(model.intercept))
    word = "pre-closure" if row["book"] == "asset" else "early withdrawal"
    higher = pred["p_early_base"] >= avg
    top = [d for d in items if d["direction"] == ("raises" if higher else "lowers") and abs(d["effect_pp"]) >= 0.5][:2]
    summary = (f"{pred['p_early_base']:.0%} chance of {word} over the contract's life, "
               f"{'above' if higher else 'below'} the {avg:.0%} of an average contract")
    summary += (", mainly because of: " + "; ".join(d["value"] for d in top) + ".") if top else "."
    if pred["p_early"] != pred["p_early_base"]:
        summary += (f" {pred['elapsed_fraction']:.0%} of its life has already passed, so the chance of an early exit "
                    f"from today is {pred['p_early']:.0%}.")
    return {"average_probability": round(avg, 4), "summary": summary, "drivers": items}


def reasons(row: dict, pred: dict) -> list[dict]:
    """Short chips for the drill-down table: the spread, then the main behavioural story."""
    f = pred["features"]
    s, inc = f["spread_pp"], f["incentive_pp"]
    chips = [{"text": f"Spread {'+' if s >= 0 else '−'}{abs(s):.1f} pp", "tone": "up" if inc > 0 else "down"}]
    if pred["p_early"] <= 0:
        chips.append({"text": "Past the exit window" if pred["elapsed_fraction"] > 0 else "Stays to maturity", "tone": "neutral"})
    elif inc >= 0.5:
        chips.append({"text": "Refinancing likely" if row["book"] == "asset" else "Better rates elsewhere", "tone": "neutral"})
    elif pred["p_early"] >= 0.3 and f["customer_segment"] in ("Retail", "SME"):
        chips.append({"text": f"{f['customer_segment']} customer", "tone": "neutral"})
    elif 0.35 <= pred["elapsed_fraction"] <= 0.5:
        chips.append({"text": "Near the 40% line", "tone": "warn"})
    elif inc <= -0.5:
        chips.append({"text": "Priced below market" if row["book"] == "asset" else "Paid above market", "tone": "neutral"})
    return chips


def driver_importance(model: Model) -> list[dict]:
    """How much each input group can move the log-odds (|coef| per std, or the range between categories)."""
    groups = [
        ("Rate vs market", abs(model.coef_num["incentive_pp"]) + abs(model.coef_num["market_rate_pct"])),
        ("Customer segment", _spread(model.coef_cat["customer_segment"])),
        ("Client rating", abs(model.coef_num["client_rating"])),
        ("Product", _spread(model.coef_cat["product"])),
        ("Location", _spread(model.coef_cat["location"])),
        ("Tenor, amount", abs(model.coef_num["tenor_years"]) + abs(model.coef_num["log_amount"])),
    ]
    top = max(v for _, v in groups) or 1.0
    out = []
    for label, v in groups:
        r = v / top
        out.append({"label": label, "strength": round(r, 3), "log_odds": round(v, 3),
                    "level": "High" if r >= 0.6 else "Med" if r >= 0.15 else "Low" if r >= 0.04 else "~0"})
    return sorted(out, key=lambda d: -d["strength"])


def _spread(d: dict[str, float]) -> float:
    return (max(d.values()) - min(d.values())) / 2 if d else 0.0


# ------------------------------------------------------------------------------------------ simulation
def simulation_seed(*parts: object) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, parts)).encode()).digest()[:4], "little")


def simulate(model: Model, rows: Sequence[dict], preds: Sequence[dict], calc_date: date, policy: str,
             n_sims: int = DEFAULT_SIMULATIONS, seed: int = 0) -> dict[tuple[str, str | None, str], dict]:
    """Monte Carlo net gap per (currency, location|None, bucket): mean, p5, p50, p95, p_shortfall."""
    if not rows:
        return {}
    buckets = det.build_buckets(calc_date)
    keys = list(det.BUCKET_KEYS)
    ends = np.array([b.end.toordinal() for b in buckets[:-1]])
    calc_o = calc_date.toordinal()
    groups: dict[tuple[str, str | None], int] = {}
    gid_loc, gid_all = [], []
    for r in rows:
        gid_loc.append(groups.setdefault((r["currency"], r["location"]), len(groups)))
        gid_all.append(groups.setdefault((r["currency"], None), len(groups)))
    gid_loc, gid_all = np.array(gid_loc), np.array(gid_all)
    sign = np.array([1.0 if r["book"] == "asset" else -1.0 for r in rows])
    t0 = np.array([r["transaction_date"].toordinal() for r in rows], dtype=float)
    mat = np.array([r["maturity_date"].toordinal() for r in rows], dtype=float)
    life = np.maximum(1.0, mat - t0)
    cash = np.array([float(r["cash_at_maturity"]) for r in rows]) * sign
    p = np.array([pr["p_early"] for pr in preds])
    elapsed = np.array([pr["elapsed_fraction"] for pr in preds])
    s = model.exit_fractions
    k = np.searchsorted(s, elapsed, side="right")
    n_tail = len(s) - k

    def bucket_of(o):
        return np.where(o < calc_o, -1, np.searchsorted(ends, o, side="left"))

    base_bucket = bucket_of(mat)
    if policy == "bucket_0_1m":
        base_bucket = np.where(base_bucket < 0, 0, base_bucket)
    rng = np.random.default_rng(seed)
    G, B = len(groups), len(keys)
    totals = np.zeros((n_sims, G, B))
    chunk = max(1, 200_000 // len(rows))
    for start in range(0, n_sims, chunk):
        n = min(chunk, n_sims - start)
        early = (rng.random((n, len(rows))) < p) & (n_tail > 0)
        pick = k + np.floor(rng.random((n, len(rows))) * np.maximum(n_tail, 1)).astype(int)
        exit_o = np.clip(np.round(t0 + s[np.minimum(pick, len(s) - 1)] * life), calc_o, mat)
        bkt = np.where(early, bucket_of(exit_o), base_bucket)
        valid = bkt >= 0
        sim_idx = np.broadcast_to(np.arange(n)[:, None], bkt.shape)
        cash_b = np.broadcast_to(cash, bkt.shape)
        for gids in (gid_loc, gid_all):
            g = np.broadcast_to(gids, bkt.shape)
            np.add.at(totals[start:start + n], (sim_idx[valid], g[valid], bkt[valid]), cash_b[valid])
    out = {}
    for (ccy, loc), g in groups.items():
        for b, key in enumerate(keys):
            v = totals[:, g, b]
            out[(ccy, loc, key)] = {"mean": float(v.mean()), "p5": float(np.percentile(v, 5)),
                                    "p50": float(np.percentile(v, 50)), "p95": float(np.percentile(v, 95)),
                                    "p_shortfall": float((v < 0).mean())}
    return out


# ------------------------------------------------------------------------------------- recommendations
# Short instrument names per bucket: (how to fund a gap there, where to park cash until then)
INSTRUMENT = {
    "0-1M": ("repo / interbank", "1M placements"),
    "1-3M": ("CDs / CP", "3M T-bills"),
    "3-12M": ("term deposits / CDs", "6M T-bills"),
    "1-5Y": ("term loans / notes", "1–5Y govt bonds"),
    ">5Y": ("senior bond issue", "long-dated bonds"),
}
TENOR = {"0-1M": "overnight", "1-3M": "3M", "3-12M": "6–12M", "1-5Y": "1–5Y term", ">5Y": "long-term"}
SHORT_TERM = ("0-1M", "1-3M")
_RANK = {"high": 0, "medium": 1, "low": 2}


def _when(b: dict) -> str:
    """Act before the bucket starts: 0-1M is now, later buckets by their first day."""
    if b["bucket"] == "0-1M":
        return " now"
    d = b.get("start_date")
    return f" by {date.fromisoformat(d):%d %b %Y}" if d else ""


def recommend(currency: str, location: str | None, buckets: Sequence[dict], *, view: str = "AI-adjusted",
              base: str = "contractual view", cause: str = "Predicted early withdrawals") -> list[dict]:
    """One-line treasury actions from the AI-adjusted (behavioural) gap, read cumulatively like a treasury desk:

    1. a surplus in an earlier bucket is rolled forward to cover a later shortfall (no new money needed);
    2. whatever is still short is raised externally, with the instrument that fits that bucket, before it starts;
    3. surplus nobody needs later is invested (or kept liquid when the simulation shows real shortfall risk);
    4. near-term buckets also get a standby line for the simulated bad case (P5) and a retention action when
       predicted early deposit withdrawals are what pushes them short.

    Rows need: bucket, det_net, ai_net, ai/det assets/liabilities; optional p5, p_shortfall, start_date."""
    scope = f"{currency}{' · ' + location if location else ''}"
    rows = sorted(buckets, key=lambda b: det.BUCKET_KEYS.index(b["bucket"]))
    money = lambda v: f"{currency} {_fmt_amt(v)}"
    out: list[dict] = []

    def add(b: dict, kind: str, severity: str, action: str, why: str, amount: float) -> None:
        out.append({"currency": currency, "location": location, "scope": scope, "bucket": b["bucket"],
                    "kind": kind, "severity": severity, "action": action, "rationale": why,
                    "amount": round(amount, 2), "deterministic_net": round(b["det_net"], 2),
                    "ai_net": round(b["ai_net"], 2)})

    pool: list[list] = []                       # [source row, surplus still unallocated], oldest first
    for b in rows:
        key, ai = b["bucket"], b["ai_net"]
        p5 = b.get("p5")
        if ai > 0:
            pool.append([b, ai])
        elif ai < 0:
            need = -ai
            park = INSTRUMENT[det.BUCKET_KEYS[det.BUCKET_KEYS.index(key) - 1]][1] if key != "0-1M" else None
            for src in pool:                    # 1. roll earlier surplus forward
                use = min(src[1], need)
                if use <= 0:
                    continue
                src[1] -= use
                need -= use
                add(b, "cover", "low", f"Roll {money(use)} of {src[0]['bucket']} surplus into {park} – covers {key} gap",
                    f"The {src[0]['bucket']} surplus is not needed before {key}, so no new funding is required for this part.",
                    use)
                if need <= 0:
                    break
            if need > 0:                        # 2. raise the rest
                add(b, "fund", "high" if key in SHORT_TERM else "medium",
                    f"Raise {TENOR[key]} funding – {money(need)} via {INSTRUMENT[key][0]}{_when(b)}",
                    f"{view} {key} gap is −{money(-ai)} ({base} {'+' if b['det_net'] >= 0 else '−'}"
                    f"{money(abs(b['det_net']))})" + (f"; earlier surplus covers {money(-ai - need)}." if -ai - need > 0
                                                      else "."), need)
        if key in SHORT_TERM and p5 is not None and p5 < min(ai, 0):   # 4a. bad-case standby
            extra = -p5 - max(0.0, -ai)
            if extra >= max(1.0, 0.05 * abs(p5)):
                add(b, "buffer", "medium", f"Arrange standby line – {money(extra)} for {key} bad case",
                    f"In the 5% worst simulated outcomes the {key} gap reaches −{money(-p5)}.", extra)
        dl = b["ai_liabilities"] - b["det_liabilities"]
        if ai < 0 and key in SHORT_TERM + ("3-12M",) and dl >= max(1.0, 0.01 * b["det_liabilities"]):   # 4b. retention
            add(b, "retain", "medium", f"Offer retention to early-withdrawal deposits – {money(dl)} in {key}",
                f"{cause} add {money(dl)} of outflows to {key}; see the contracts list for the clients.", dl)

    for src, left in pool:                      # 3. surplus nobody needs later
        if left <= 0:
            continue
        key, p_short, p5 = src["bucket"], src.get("p_shortfall") or 0.0, src.get("p5")
        if p5 is not None and p5 < 0:           # might turn into a gap: invest one tenor shorter than usual
            shorter = INSTRUMENT[det.BUCKET_KEYS[max(0, det.BUCKET_KEYS.index(key) - 1)]][1]
            add(src, "keep_liquid", "medium",
                f"Invest {key} surplus short – {money(left)} in {shorter} ({p_short:.0%} shortfall risk)",
                f"Expected surplus, but the bad case is −{money(-p5)}, so do not lock it into {INSTRUMENT[key][1]}.", left)
        else:
            add(src, "invest", "low", f"Invest surplus – {money(left)} in {INSTRUMENT[key][1]}",
                f"Not needed by any later bucket{f'; shortfall risk {p_short:.0%}' if p_short else ''}.", left)

    kinds = ["fund", "retain", "buffer", "keep_liquid", "cover", "invest"]
    out.sort(key=lambda r: (_RANK[r["severity"]], det.BUCKET_KEYS.index(r["bucket"]), kinds.index(r["kind"])))
    return out


def assumptions(model: Model, n_sims: int) -> list[str]:
    s = model.exit_fractions
    m = model.metrics
    return [
        f"Model {model.name}: logistic regression written from scratch (Newton-Raphson, L2 = {L2}), trained on all "
        f"{model.n_train:,} closed historical contracts ({model.n_early:,} early exits, {model.base_rate:.1%}); "
        f"{m.get('open_rows', 0):,} still-open contracts have no outcome yet and are scored, not trained on.",
        "Main driver: rate incentive = loan rate − market rate (loans) or market rate − deposit rate (deposits).",
        f"Exit timing from history: early exits happened between {s.min():.0%} and {s.max():.0%} of the contract's "
        f"life (median {np.median(s):.0%}).",
        "Each live contract's probability is conditioned on the part of its life already passed.",
        "Adjusted maturity = contractual maturity − p(early) × (contractual − expected early-exit date).",
        "Cash amounts are unchanged (principal + contractual interest): only the date moves, so total net gap is "
        "identical in both views.",
        f"Range: {n_sims:,} Monte Carlo runs (fixed seed per snapshot); P5–P95 of the net gap per bucket.",
        "Buckets, past-due policy and currency handling are identical to the deterministic ladder.",
    ]
