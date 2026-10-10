import { useEffect, useState } from "react";
import { downloadHistoryPredictions, getModelCard, type ApiError, type Classification, type ModelCard } from "../api/client";
import { formatInt, formatPct } from "../lib/format";

function ClassRow({ name, c }: { name: string; c?: Classification }) {
  if (!c) return null;
  return (
    <li><span>{name}</span><span className="mono">{formatPct(c.accuracy, 1)}</span><span className="mono">{formatPct(c.precision, 1)}</span>
      <span className="mono">{formatPct(c.recall, 1)}</span><span className="mono">{c.f1.toFixed(3)}</span></li>
  );
}

/** What the behavioural model learned, how well it did on newer contracts, and its assumptions. */
export function ModelCardPanel() {
  const [card, setCard] = useState<ModelCard | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { getModelCard().then(setCard, (e: ApiError) => setErr(e.message)); }, []);
  if (err) return <p className="tbl-error" role="alert">{err}</p>;
  if (!card) return <p className="loading">Loading model…</p>;
  const m = card.metrics;
  const top = card.drivers.slice(0, 8);
  const maxC = Math.max(...top.map((d) => Math.abs(d.coefficient)), 0.01);
  const maxH = Math.max(...card.exit_timing.histogram.map((b) => b.count), 1);
  const exportAll = () => { setBusy(true); downloadHistoryPredictions().catch((e: ApiError) => setErr(e.message)).finally(() => setBusy(false)); };
  return (
    <div className="modelcard">
      <div className="mc-grid">
        <div><span>Trained on</span><b>{formatInt(card.trained_on)}</b><small>every closed contract · {card.historical_batch.filename}</small></div>
        <div><span>Early-exit rate</span><b>{formatPct(card.early_exit_rate, 1)}</b><small>{formatInt(card.early_exits)} early exits · {formatInt(m.open_rows ?? 0)} open contracts scored</small></div>
        <div><span>AUC (holdout)</span><b>{m.auc ?? "–"}</b><small>{m.holdout ?? "no holdout"} · 0.5 = guessing</small></div>
        <div><span>Brier score</span><b>{m.brier ?? "–"}</b><small>vs {m.brier_baseline ?? "–"} for a flat average (lower is better)</small></div>
      </div>
      <p className="meta">{card.type}; converged in {card.iterations} Newton steps. Holdout: {formatInt(m.test_rows ?? 0)} newest contracts, {formatPct(m.test_early_rate ?? 0)} of which left early.</p>

      <h3>Accuracy and F1 on the holdout</h3>
      <ul className="calib clf">
        <li className="calib-head"><span>Cut-off</span><span>Accuracy</span><span>Precision</span><span>Recall</span><span>F1</span></li>
        <ClassRow name="50% (default)" c={m.at_50} />
        <ClassRow name={`${formatPct(m.best_f1?.threshold ?? 0)} (best F1)`} c={m.best_f1} />
      </ul>
      <p className="meta">The ladder uses the probabilities directly; these yes/no scores only show how well the model ranks early exits.</p>

      <div className="mc-cols">
        <div>
          <h3>Strongest drivers</h3>
          <ul className="coefs">
            {top.map((d) => (
              <li key={d.feature}>
                <span>{d.label}</span>
                <span className="drv-bar" aria-hidden="true"><i data-dir={d.coefficient > 0 ? "raises" : "lowers"} style={{ width: `${(Math.abs(d.coefficient) / maxC) * 50}%`, [d.coefficient >= 0 ? "left" : "right"]: "50%" }} /></span>
                <span className="mono">{d.coefficient > 0 ? "+" : "−"}{Math.abs(d.coefficient).toFixed(2)}</span>
              </li>
            ))}
          </ul>
          <p className="meta">Log-odds per standard deviation (numbers) or vs the average mix (categories).</p>
        </div>
        <div>
          <h3>When early exits happen</h3>
          <div className="hist" role="img" aria-label={`Early exits happen between ${formatPct(card.exit_timing.min)} and ${formatPct(card.exit_timing.max)} of a contract's life, median ${formatPct(card.exit_timing.median)}`}>
            {card.exit_timing.histogram.map((b) => (
              <div key={b.from} title={`${formatPct(b.from)}–${formatPct(b.to)} of life: ${formatInt(b.count)}`}>
                <i style={{ height: `${(b.count / maxH) * 100}%` }} />
                <span>{Math.round(b.from * 100)}</span>
              </div>
            ))}
          </div>
          <p className="meta">% of the contract's life passed at the early exit (median {formatPct(card.exit_timing.median)}).</p>
        </div>
        {m.calibration && (
          <div>
            <h3>Predicted vs actual (holdout)</h3>
            <ul className="calib" aria-label="Predicted versus actual early-exit rate per risk group">
              <li className="calib-head"><span>Risk group</span><span>Predicted</span><span>Actual</span></li>
              {m.calibration.map((c, i) => (
                <li key={i}><span>{i + 1} of {m.calibration!.length}</span><span className="mono">{formatPct(c.predicted)}</span><span className="mono">{formatPct(c.observed)}</span></li>
              ))}
            </ul>
          </div>
        )}
      </div>
      <h3>Assumptions</h3>
      <ul>{card.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      <button type="button" className="btn secondary ai-outline" onClick={exportAll} disabled={busy}>{busy ? "Exporting…" : `Export predictions for all ${formatInt(m.history_rows ?? 0)} historical rows (CSV)`}</button>
    </div>
  );
}
