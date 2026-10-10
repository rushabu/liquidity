import { useEffect, useMemo, useRef, useState } from "react";
import { getAiLadder, getLadderCached, type AiLadderResponse, type ApiError, type BucketKey, type LadderResponse, type LadderRow } from "../../api/client";
import { formatInt, formatMoney, formatPct, formatSignedCompact } from "../../lib/format";
import { GapChart } from "../GapChart";
import type { DrillPreset, PageMeta, Shared } from "./types";

const pick = (src: LadderRow[] | undefined, currency: string, location: string) =>
  (src ?? []).filter((r) => r.currency === currency && (location ? r.location === location : r.scope === "currency_total"));

/** The AI-adjusted ladder itself: assets, liabilities and net gap per bucket (use case 5b), next to the deterministic net. */
export function LadderView({ shared, onDrill, onMeta }: { shared: Shared; onDrill: (p: DrillPreset) => void; onMeta: (m: PageMeta) => void }) {
  const { currency, setCurrency, location, setLocation } = shared;
  const [ai, setAi] = useState<AiLadderResponse | null>(null);
  const [base, setBase] = useState<LadderResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const seq = useRef(0);
  useEffect(() => {
    const mine = ++seq.current;
    Promise.all([getAiLadder(), getLadderCached()]).then(([a, b]) => {
      if (mine !== seq.current) return;
      setAi(a); setBase(b); onMeta(a.metadata);
      if (!currency) setCurrency(a.ladder[0]?.currency ?? "");
    }, (e: ApiError) => { if (mine === seq.current) setError(e); });
  }, []);   // eslint-disable-line react-hooks/exhaustive-deps

  const ccy = currency || ai?.ladder[0]?.currency || "";
  const rows = useMemo(() => pick(ai?.ladder, ccy, location), [ai, ccy, location]);
  const detRows = useMemo(() => pick(base?.ladder, ccy, location), [base, ccy, location]);
  const currencies = useMemo(() => [...new Set((ai?.ladder ?? []).map((r) => r.currency))], [ai]);
  const locations = useMemo(() => [...new Set((ai?.ladder ?? []).filter((r) => r.currency === ccy && r.location).map((r) => r.location as string))].sort(), [ai, ccy]);
  if (error) return <div className="banner" role="alert">{error.message}</div>;
  if (!ai) return <p className="loading">Building the AI-adjusted ladder…</p>;
  const md = ai.metadata;
  const detNet = (b: BucketKey) => detRows.find((r) => r.bucket === b)?.net_gap ?? 0;
  const go = (bucket: BucketKey) => onDrill({ bucket, currency: ccy, moved_only: false });

  return (
    <>
      <section className="kpi-row" aria-label="Model summary">
        <div className="kpi"><span>Contracts changing bucket</span><b>{formatInt(md.moved_count)}</b><small>{formatInt(md.shifted_count)} of {formatInt(md.transactions_read)} have an earlier date</small></div>
        <div className="kpi"><span>Average early-exit chance</span><b>{formatPct(md.average_p_early)}</b><small>from today</small></div>
        <div className="kpi"><span>Average move</span><b>{formatInt(Math.round(md.average_shift_days))} days</b><small>earlier, when moved</small></div>
        <div className="kpi"><span>Model</span><b>AUC {md.model.holdout_auc ?? "–"}</b><small>F1 {md.model.holdout_f1 ?? "–"} · {formatInt(md.model.trained_on)} contracts</small></div>
      </section>
      <section className="card" aria-label="AI-adjusted ladder">
        <div className="table-head">
          <div role="tablist" aria-label="Currency" className="filters pills">
            {currencies.map((c) => (
              <button key={c} role="tab" type="button" aria-selected={c === ccy} className={c === ccy ? "btn ai" : "btn secondary"} onClick={() => { setCurrency(c); setLocation(""); }}>{c}</button>
            ))}
          </div>
          <div className="filters">
            <label>Location
              <select value={location} onChange={(e) => setLocation(e.target.value)}>
                <option value="">All locations</option>{locations.map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
            </label>
          </div>
        </div>
        <GapChart rows={rows} currency={ccy} onSelect={go} scope={`${location || md.scope_location || "all locations"} · AI-adjusted maturities`} />
        <div className="table-wrap">
          <table>
            <thead><tr><th>Bucket</th><th className="num">Assets</th><th className="num">Liabilities</th><th className="num">Net gap (AI)</th><th className="num">Deterministic</th><th className="num">Shift</th><th className="num">Txns</th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const delta = r.net_gap - detNet(r.bucket);
                return (
                  <tr key={r.bucket}>
                    <td><button type="button" className="link-btn ai" onClick={() => go(r.bucket)}>{r.bucket}</button></td>
                    <td className="num mono">{formatMoney(r.asset_cash_at_maturity)}</td>
                    <td className="num mono">{formatMoney(r.liability_cash_at_maturity)}</td>
                    <td className="num mono" style={{ fontWeight: 600 }}>{formatMoney(r.net_gap)}</td>
                    <td className="num mono muted">{formatMoney(detNet(r.bucket))}</td>
                    <td className="num mono" data-tone={delta < 0 ? "shortfall" : delta > 0 ? "surplus" : undefined}>{formatSignedCompact(delta)}</td>
                    <td className="num mono">{formatInt(r.asset_count + r.liability_count)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
        <p className="meta">Same buckets and rules as the deterministic ladder; each contract sits at its AI-adjusted maturity with its cash amount unchanged.</p>
      </section>
      <details className="card">
        <summary><b>Assumptions</b></summary>
        <ul>{ai.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
      </details>
    </>
  );
}
