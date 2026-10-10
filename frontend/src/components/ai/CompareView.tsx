import { useEffect, useMemo, useRef, useState } from "react";
import { getAiLadder, getAiTransactions, getComparison, getLadderCached, getWhy, type ApiError, type CompareResponse, type Recommendation, type Segment, type Side } from "../../api/client";
import { formatInt, formatMoney, formatPct, formatSignedCompact } from "../../lib/format";
import { useSticky } from "../../lib/sticky";
import { MonthlyChart } from "./MonthlyChart";
import type { DrillPreset, PageMeta, Shared } from "./types";

const SEVERITY = { high: "error", medium: "warning", low: "success" } as const;
const KIND: Record<Recommendation["kind"], string> = {
  fund: "Fund", cover: "Roll over", retain: "Retain", buffer: "Standby", keep_liquid: "Invest short", invest: "Invest",
};

export function Actions({ items, empty, compact = false }: { items: Recommendation[]; empty: string; compact?: boolean }) {
  if (!items.length) return <p className="empty">{empty}</p>;
  return (
    <ol className="actions">
      {items.map((r) => (
        <li key={`${r.scope}-${r.bucket}-${r.action}`}>
          <span className="chip" data-tone={SEVERITY[r.severity]} title={`${r.severity} priority`}>{KIND[r.kind] ?? r.severity}</span>
          <div>
            <b>{r.action}</b> <span className="meta">{r.scope} · {r.bucket}</span>
            <p>{compact ? r.rationale.split(/(?<=\.)\s/)[0] : r.rationale}</p>
          </div>
        </li>
      ))}
    </ol>
  );
}

/** Warm the other tabs in the background (the server has the snapshot hot now), so they open instantly.
 *  Same parameters as their first request, so the response store answers them from memory. */
function prefetch(currency: string, location: string) {
  const loc = location || undefined;
  const quiet = () => undefined;
  getWhy({ currency, location: loc, market_shift_pp: 0 }).catch(quiet);
  getAiLadder().catch(quiet);
  getLadderCached().catch(quiet);
  getAiTransactions({ currency, location: loc, bucket: "1-3M", p_min: 0, p_max: 1, moved_only: true, sort: "p_early", order: "desc", page: 1, page_size: 10 }).catch(quiet);
}

export function CompareView({ shared, onDrill, onMeta }: { shared: Shared; onDrill: (p: DrillPreset) => void; onMeta: (m: PageMeta) => void }) {
  const { currency, setCurrency, location, setLocation } = shared;
  const [side, setSide] = useSticky<Side | "">("ai.cmp.side", "");
  const [segment, setSegment] = useSticky<Segment | "">("ai.cmp.segment", "");
  const [data, setData] = useState<CompareResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [month, setMonth] = useSticky<number | null>("ai.cmp.month", null);
  const seq = useRef(0);

  const loaded = useRef("");                               // params the data on screen belongs to
  useEffect(() => {
    const want = JSON.stringify([currency, location, side, segment]);
    if (data && want === loaded.current) return;             // e.g. currency "" just resolved to EUR: same data
    const mine = ++seq.current;
    setError(null);
    getComparison({ currency: currency || undefined, location: location || undefined, side: side || undefined, segment: segment || undefined })
      .then((d) => {
        if (mine !== seq.current) return;
        loaded.current = JSON.stringify([d.metadata.currency, location, side, segment]);
        setData(d); onMeta(d.metadata);
        prefetch(d.metadata.currency, location);
        if (!currency) setCurrency(d.metadata.currency);
        setMonth((m) => m ?? d.kpis.deepest_opposite.month);
      }, (e: ApiError) => { if (mine === seq.current) setError(e); });
  }, [currency, location, side, segment]);   // eslint-disable-line react-hooks/exhaustive-deps

  const sel = useMemo(() => data?.months.find((m) => m.month === month) ?? null, [data, month]);
  if (error) return <div className="banner" role="alert">{error.message}</div>;
  if (!data) return <p className="loading">Comparing the two views and running 1,000 simulations…</p>;
  const md = data.metadata, k = data.kpis;
  const ccy = md.currency;

  return (
    <>
      <section className="card ai-filters" aria-label="Filters">
        <div role="tablist" aria-label="Currency" className="filters pills">
          {md.currencies.map((c) => (
            <button key={c} role="tab" type="button" aria-selected={c === ccy} className={c === ccy ? "btn ai" : "btn secondary"}
                    onClick={() => { setCurrency(c); setLocation(""); setMonth(null); }}>{c}</button>
          ))}
        </div>
        <div className="filters">
          <label>Location
            <select value={location} onChange={(e) => setLocation(e.target.value)}>
              <option value="">All locations</option>
              {md.locations.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
          </label>
          <label>Side
            <select value={side} onChange={(e) => setSide(e.target.value as Side | "")}>
              <option value="">Assets + liabilities</option>
              <option value="asset">Assets (loans)</option>
              <option value="liability">Liabilities (deposits)</option>
            </select>
          </label>
          <label>Segment
            <select value={segment} onChange={(e) => setSegment(e.target.value as Segment | "")}>
              <option value="">All</option>
              {["Retail", "Corporate", "SME", "Institutional"].map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </label>
        </div>
      </section>

      <section className="kpi-row" aria-label="Key figures">
        <div className="kpi"><span>Contracts moved earlier</span><b>{formatInt(k.moved)} / {formatInt(k.total)}</b><small>changed bucket · {formatInt(k.shifted)} date moved</small></div>
        <div className="kpi"><span>Biggest gain in early cash</span><b className="pos">{formatSignedCompact(k.biggest_gain.amount)} · M{k.biggest_gain.month}</b></div>
        <div className="kpi"><span>Deepest opposite shift</span><b className="neg">{formatSignedCompact(k.deepest_opposite.amount)} · M{k.deepest_opposite.month}</b></div>
        <div className="kpi"><span>Total net gap</span><b className={k.ai_total < 0 ? "neg" : "pos"}>{formatSignedCompact(k.ai_total)} {Math.abs(k.ai_total - k.det_total) < 1 ? "(same)" : ""}</b><small>only timing changes</small></div>
      </section>

      <div className="ai-grid">
        <section className="card" aria-label="Cumulative net gap by month">
          <MonthlyChart months={data.months} currency={ccy} selected={month} onSelect={setMonth} />
        </section>
        <section className="card" aria-label="Bucket comparison">
          <h2>Bucket comparison</h2>
          <p className="hint">Net gap, {ccy}{location ? ` · ${location}` : ""}</p>
          <div className="table-wrap">
            <table className="compact">
              <thead><tr><th>Bucket</th><th className="num">Determ.</th><th className="num">AI</th><th className="num">Shift</th></tr></thead>
              <tbody>
                {data.buckets.map((b) => (
                  <tr key={b.bucket} data-selected={sel?.bucket === b.bucket ? "" : undefined}>
                    <td><button type="button" className="link-btn ai" onClick={() => onDrill({ bucket: b.bucket, currency: ccy, side: side || undefined, segment: segment || undefined, moved_only: true })}>{b.bucket}</button></td>
                    <td className="num mono">{formatSignedCompact(b.det_net)}</td>
                    <td className="num mono">{formatSignedCompact(b.ai_net)}</td>
                    <td className="num mono" data-tone={b.delta < 0 ? "shortfall" : b.delta > 0 ? "surplus" : undefined}>{formatSignedCompact(b.delta)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {sel && sel.month > 0 && (
            <div className="selected-month">
              <b>Selected: month {sel.month} ({formatSignedCompact(sel.diff)})</b>
              <p>{sel.note} <button type="button" className="link-btn ai" onClick={() => onDrill({ bucket: sel.bucket ?? undefined, currency: ccy, moved_only: true })}>See why → Drill down →</button></p>
              <small className="meta">Month ends {sel.end_date}, in bucket {sel.bucket}.</small>
            </div>
          )}
          {data.buckets[0].p5 !== null && (
            <details className="ranges">
              <summary>Simulated range (P5–P95)</summary>
              <table className="compact">
                <thead><tr><th>Bucket</th><th className="num">P5</th><th className="num">P95</th><th className="num">Shortfall</th></tr></thead>
                <tbody>{data.buckets.map((b) => (
                  <tr key={b.bucket}><td>{b.bucket}</td><td className="num mono">{formatSignedCompact(b.p5 ?? 0)}</td><td className="num mono">{formatSignedCompact(b.p95 ?? 0)}</td><td className="num mono">{formatPct(b.p_shortfall ?? 0)}</td></tr>
                ))}</tbody>
              </table>
            </details>
          )}
        </section>
      </div>

      <section className="card" aria-label="Recommended actions">
        <div className="table-head">
          <div>
            <h2>Recommended actions · {ccy}{location ? ` · ${location}` : ""}</h2>
            <p className="hint">From the AI-adjusted gap of each bucket and its simulated bad case.</p>
          </div>
        </div>
        <Actions items={data.recommendations} empty="No action needed." />
        {data.overview.length > 0 && (
          <details className="overview">
            <summary><b>Top actions across all currencies</b></summary>
            <Actions compact items={data.overview.slice(0, 5)} empty="" />
          </details>
        )}
        <ul className="meta notes">{data.notes.map((n) => <li key={n}>{n}</li>)}<li>Total {formatMoney(k.ai_total)} {ccy} in both views.</li></ul>
      </section>
    </>
  );
}
