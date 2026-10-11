import { useEffect, useRef, useState } from "react";
import {
  downloadAiPredictions, getAiExplanation, getAiTransactions, type AiExplanation, type AiTxnPage, type AiTxnQuery,
  type ApiError, type BucketKey, type Segment, type Side,
} from "../../api/client";
import { formatInt, formatMoney, formatPct, formatSignedCompact } from "../../lib/format";
import { useSticky } from "../../lib/sticky";
import type { DrillPreset, Shared } from "./types";

const PAGE = 10;
const BUCKETS: BucketKey[] = ["0-1M", "1-3M", "3-12M", "1-5Y", ">5Y"];
const P_RANGES = { all: [0, 1], "0-20": [0, 0.2], "20-40": [0.2, 0.4], "40-100": [0.4, 1], "60-100": [0.6, 1] } as const;
const SPREADS = { all: [undefined, undefined], "gt1": [1, undefined], "gt0": [0, undefined], "lt0": [undefined, 0], "ltm1": [undefined, -1] } as const;
type PRange = keyof typeof P_RANGES;
type SRange = keyof typeof SPREADS;
const sign = (n: number, d = 1) => `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(d)}`;

export function WhyPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const [e, setE] = useState<AiExplanation | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setE(null); setErr(null);
    getAiExplanation(id).then((d) => live && setE(d), (x: ApiError) => live && setErr(x.message));
    return () => { live = false; };
  }, [id]);
  const maxLo = Math.max(0.05, ...(e?.rationale.drivers ?? []).map((d) => Math.abs(d.log_odds)));
  const groups = e ? [
    ...e.rationale.drivers.filter((d) => !["tenor_years", "log_amount"].includes(d.feature) && Math.abs(d.log_odds) >= 0.01).slice(0, 4),
  ] : [];
  const tenorAmount = e ? e.rationale.drivers.filter((d) => ["tenor_years", "log_amount"].includes(d.feature)).reduce((s, d) => s + d.log_odds, 0) : 0;
  return (
    <aside className="card why-panel" aria-label={`Why ${id} moved`}>
      <div className="why-head">
        <h2>Why {id} {e && e.shift_days > 0 ? "moved" : "stays"}</h2>
        {e && <span className="chip" data-tone="warning">P(early) {formatPct(e.p_early)}</span>}
        <button type="button" className="close-x" aria-label="Close" onClick={onClose}>×</button>
      </div>
      {err && <p className="tbl-error" role="alert">{err}</p>}
      {!e && !err && <p className="loading">Loading…</p>}
      {e && (
        <>
          <p className="hint">{e.type === "asset" ? "Asset" : "Liability"} · {e.customer_segment} · {e.interest_rate_pct.toFixed(2)}% vs market {e.market_rate_pct.toFixed(2)}%</p>
          <ul className="lo-bars">
            {groups.map((d) => (
              <li key={d.feature}>
                <span title={d.value}>{d.feature === "incentive_pp" ? `Rate spread ${sign(e.spread_pp)} pp` : d.feature === "customer_segment" ? `Segment: ${e.customer_segment}` : d.label}</span>
                <span className="lo-bar" aria-hidden="true"><i data-dir={d.direction} style={{ width: `${(Math.abs(d.log_odds) / maxLo) * 50}%`, [d.log_odds >= 0 ? "left" : "right"]: "50%" }} /></span>
                <b className="mono">{sign(d.log_odds, 2)}</b>
              </li>
            ))}
            <li><span>Tenor, amount</span><span className="lo-bar" aria-hidden="true"><i data-dir="neutral" style={{ width: "1px", left: "50%" }} /></span><b className="mono">{Math.abs(tenorAmount) < 0.01 ? "~0" : sign(tenorAmount, 2)}</b></li>
          </ul>
          <p className="meta">Log-odds contribution vs an average contract. Red pushes towards leaving early.</p>
          <dl className="exit-facts">
            <div><dt>Contractual maturity</dt><dd>{e.contractual_maturity_date} · {e.deterministic_bucket ?? "past due"}</dd></div>
            <div><dt>Predicted exit</dt><dd className={e.shift_days > 0 ? "pos" : undefined}>{e.adjusted_maturity_date} · {e.ai_bucket ?? "past due"}</dd></div>
            <div><dt>Cash amount</dt><dd>{formatMoney(e.cash_at_maturity)}<small>(unchanged)</small></dd></div>
          </dl>
          <p className="meta">{e.rationale.summary}</p>
          <code className="formula">{e.formula}</code>
        </>
      )}
    </aside>
  );
}

/** Cash pulled into the bucket. One figure only for a single currency; several currencies are listed side by
 *  side, never added together (there is no FX rate). */
function CashPulled({ kpis, bucket }: { kpis: AiTxnPage["kpis"]; bucket: string }) {
  const by = Object.entries(kpis.cash_pulled_in_by_currency ?? {});
  const label = bucket ? `Cash pulled into ${bucket}` : "Cash pulled in";
  if (!bucket) return <div className="kpi"><span>{label}</span><b>—</b><small>pick a bucket</small></div>;
  if (kpis.cash_pulled_in !== null) {
    const ccy = kpis.currencies?.[0] ?? "";
    return <div className="kpi"><span>{label}</span><b className={kpis.cash_pulled_in < 0 ? "neg" : "pos"}>{formatSignedCompact(kpis.cash_pulled_in)}</b><small>{ccy}</small></div>;
  }
  return (
    <div className="kpi"><span>{label}</span>
      <ul className="ccy-split" aria-label="By currency">
        {by.map(([c, v]) => <li key={c}><span>{c}</span> <b className={v < 0 ? "neg" : "pos"}>{formatSignedCompact(v)}</b></li>)}
        {!by.length && <li>—</li>}
      </ul>
      <small>per currency, not added (no FX rate)</small>
    </div>
  );
}

export function DrillView({ shared, preset }: { shared: Shared; preset: DrillPreset | null }) {
  const { currency, setCurrency, location } = shared;
  const [bucket, setBucket] = useSticky<BucketKey | "">("ai.drill.bucket", preset?.bucket ?? "1-3M");
  const [side, setSide] = useSticky<Side | "">("ai.drill.side", preset?.side ?? "");
  const [segment, setSegment] = useSticky<Segment | "">("ai.drill.segment", preset?.segment ?? "");
  const [pRange, setPRange] = useSticky<PRange>("ai.drill.p", "all");
  const [sRange, setSRange] = useSticky<SRange>("ai.drill.spread", "all");
  const [moved, setMoved] = useSticky<boolean>("ai.drill.moved", preset?.moved_only ?? true);
  const [sort, setSort] = useSticky<"p_early" | "shift_days" | "spread_pp" | "cash_at_maturity">("ai.drill.sort", "p_early");
  const [page, setPage] = useSticky("ai.drill.page", 1);
  const [appliedPreset, setAppliedPreset] = useSticky<number | null>("ai.drill.applied", null);
  const [data, setData] = useState<AiTxnPage | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [selected, setSelected] = useSticky<string | null>("ai.drill.selected", null);
  const [exporting, setExporting] = useState(false);
  const seq = useRef(0);

  useEffect(() => {
    if (!preset || (preset.id !== undefined && preset.id === appliedPreset)) return;   // apply each click once
    setAppliedPreset(preset.id ?? null);
    setPRange("all"); setSRange("all");
    if (preset.bucket) setBucket(preset.bucket);
    if (preset.currency) setCurrency(preset.currency);
    setSide(preset.side ?? ""); setSegment(preset.segment ?? ""); setMoved(preset.moved_only ?? true);
  }, [preset]);   // eslint-disable-line react-hooks/exhaustive-deps

  const query: AiTxnQuery = {
    currency: currency || undefined, location: location || undefined, bucket: bucket || undefined, type: side || undefined,
    segment: segment || undefined, p_min: P_RANGES[pRange][0], p_max: P_RANGES[pRange][1],
    spread_min: SPREADS[sRange][0], spread_max: SPREADS[sRange][1], moved_only: moved,
  };
  const key = JSON.stringify(query);
  const firstKey = useRef(key);
  useEffect(() => { if (key !== firstKey.current) { firstKey.current = key; setPage(1); } }, [key]);   // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const mine = ++seq.current;
    setErr(null);
    getAiTransactions({ ...query, sort, order: "desc", page, page_size: PAGE })
      .then((d) => {
        if (mine !== seq.current) return;
        setData(d);
        setSelected((s) => (s && d.items.some((t) => t.transaction_id === s) ? s : d.items[0]?.transaction_id ?? null));
      }, (e: ApiError) => { if (mine === seq.current) setErr(e.message); });
  }, [key, sort, page]);   // eslint-disable-line react-hooks/exhaustive-deps

  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE)) : 1;
  const exportCsv = () => { setExporting(true); downloadAiPredictions(query).catch((e: ApiError) => setErr(e.message)).finally(() => setExporting(false)); };

  return (
    <>
      <section className="card ai-filters drill-filters" aria-label="Drill-down filters">
        <label>AI bucket
          <select value={bucket} onChange={(e) => setBucket(e.target.value as BucketKey | "")}>
            <option value="">All</option>{BUCKETS.map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
        </label>
        <label>Currency
          <select value={currency} onChange={(e) => setCurrency(e.target.value)}>
            <option value="">All</option>{["EUR", "GBP", "HKD", "SGD", "USD"].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
        <label>Side
          <select value={side} onChange={(e) => setSide(e.target.value as Side | "")}>
            <option value="">All</option><option value="asset">Assets</option><option value="liability">Liabilities</option>
          </select>
        </label>
        <label>Segment
          <select value={segment} onChange={(e) => setSegment(e.target.value as Segment | "")}>
            <option value="">All</option>{["Retail", "Corporate", "SME", "Institutional"].map((s) => <option key={s} value={s}>{s}</option>)}
          </select>
        </label>
        <label>P(early) range
          <select value={pRange} onChange={(e) => setPRange(e.target.value as PRange)}>
            <option value="all">All</option><option value="0-20">0% – 20%</option><option value="20-40">20% – 40%</option>
            <option value="40-100">40% – 100%</option><option value="60-100">60% – 100%</option>
          </select>
        </label>
        <label>Spread
          <select value={sRange} onChange={(e) => setSRange(e.target.value as SRange)}>
            <option value="all">All</option><option value="gt1">&gt; +1.0 pp</option><option value="gt0">&gt; 0 pp</option>
            <option value="lt0">&lt; 0 pp</option><option value="ltm1">&lt; −1.0 pp</option>
          </select>
        </label>
        <label>Moved only
          <select value={moved ? "yes" : "no"} onChange={(e) => setMoved(e.target.value === "yes")}>
            <option value="yes">Yes</option><option value="no">No</option>
          </select>
        </label>
        <button type="button" className="btn secondary ai-outline" onClick={exportCsv} disabled={exporting}>{exporting ? "Exporting…" : "Export CSV"}</button>
      </section>

      {data && (
        <section className="kpi-row" aria-label="Selection">
          <div className="kpi"><span>Contracts in selection</span><b>{formatInt(data.kpis.count)}</b></div>
          <CashPulled kpis={data.kpis} bucket={bucket} />
          <div className="kpi"><span>Avg P(early)</span><b>{formatPct(data.kpis.avg_p_early)}</b></div>
          <div className="kpi"><span>Avg spread</span><b>{sign(data.kpis.avg_spread_pp)} pp</b></div>
        </section>
      )}

      <div className="ai-grid drill-grid">
        <section className="card" aria-label="Contracts">
          <div className="table-head">
            <div><h2>Contracts</h2><p className="hint">Click a row to see why. Sort by any column.</p></div>
          </div>
          {err && <p className="tbl-error" role="alert">{err}</p>}
          {!data && !err && <p className="loading">Loading…</p>}
          {data && data.total === 0 && <p className="empty">No contracts match these filters.</p>}
          {data && data.total > 0 && (
            <>
              <div className="table-wrap">
                <table className="drill-table">
                  <thead>
                    <tr>
                      <th>Contract</th><th>Side · Segment</th>
                      <th className="num"><button type="button" className="th-sort" onClick={() => setSort("spread_pp")}>Contract vs market{sort === "spread_pp" ? " ↓" : ""}</button></th>
                      <th><button type="button" className="th-sort" onClick={() => setSort("p_early")}>P(early){sort === "p_early" ? " ↓" : ""}</button></th>
                      <th><button type="button" className="th-sort" onClick={() => setSort("shift_days")}>Contractual → AI{sort === "shift_days" ? " ↓" : ""}</button></th>
                      <th>Main reason</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.items.map((t) => (
                      <tr key={t.transaction_id} data-selected={selected === t.transaction_id ? "" : undefined} onClick={() => setSelected(t.transaction_id)} className="clickable">
                        <td><button type="button" className="link-btn ai" aria-label={`Why ${t.transaction_id}`} onClick={(e) => { e.stopPropagation(); setSelected(t.transaction_id); }}>{t.transaction_id}</button></td>
                        <td>{t.book === "asset" ? "Asset" : "Liability"} · {t.customer_segment}</td>
                        <td className="num mono nowrap">{t.interest_rate_pct.toFixed(1)}% vs {t.market_rate_pct.toFixed(1)}%</td>
                        <td className="pcell"><span className="pbar"><i style={{ width: `${Math.round(t.p_early * 100)}%` }} /></span><span className="mono">{formatPct(t.p_early)}</span></td>
                        <td className="mono nowrap">{t.deterministic_bucket ?? "—"} → {t.bucket ?? "—"}</td>
                        <td className="chips">{t.reasons.map((r) => <span key={r.text} className="rchip" data-tone={r.tone}>{r.text}</span>)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <div className="pager">
                <span>Showing {data.items.length} of {formatInt(data.total)} · page {page} of {pages}</span>
                <div>
                  <button type="button" className="btn secondary" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
                  <button type="button" className="btn secondary" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
                </div>
              </div>
            </>
          )}
        </section>
        {selected ? <WhyPanel id={selected} onClose={() => setSelected(null)} /> : <aside className="card why-panel"><p className="empty">Select a contract to see why it moved.</p></aside>}
      </div>
    </>
  );
}
