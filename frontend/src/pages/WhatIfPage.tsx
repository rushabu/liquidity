import { useEffect, useMemo, useRef, useState } from "react";
import { getWhatIf, type ApiError, type WhatIfBase, type WhatIfBucket, type WhatIfQuery, type WhatIfResponse } from "../api/client";
import { Actions } from "../components/ai/CompareView";
import { formatInt, formatMoney, formatSignedCompact } from "../lib/format";
import { useSticky } from "../lib/sticky";

type Unit = "days" | "weeks" | "months";
type Dir = "earlier" | "later";
interface Shift { n: number; unit: Unit; dir: Dir }

const NONE: Shift = { n: 0, unit: "months", dir: "earlier" };
const MAX: Record<Unit, number> = { days: 365, weeks: 52, months: 24 };

/** {2, weeks, later} -> {months: 0, days: 14}; {1, months, earlier} -> {months: -1, days: 0} */
export function toParams(s: Shift): { months: number; days: number } {
  const n = Math.max(0, Math.min(MAX[s.unit], Math.round(s.n || 0))) * (s.dir === "earlier" ? -1 : 1);
  if (s.unit === "months") return { months: n, days: 0 };
  return { months: 0, days: s.unit === "weeks" ? n * 7 : n };
}

const PRESETS: { label: string; asset: Shift; liability: Shift }[] = [
  { label: "Liabilities 1 month earlier", asset: NONE, liability: { n: 1, unit: "months", dir: "earlier" } },
  { label: "Assets 2 weeks later", asset: { n: 2, unit: "weeks", dir: "later" }, liability: NONE },
  { label: "Stress: both", asset: { n: 2, unit: "weeks", dir: "later" }, liability: { n: 1, unit: "months", dir: "earlier" } },
  { label: "Reset", asset: NONE, liability: NONE },
];

function ShiftControl({ id, title, hint, value, onChange }: { id: string; title: string; hint: string; value: Shift; onChange: (s: Shift) => void }) {
  return (
    <fieldset className="shift-control">
      <legend>{title}</legend>
      <div className="shift-inputs">
        <label htmlFor={`${id}-n`} className="sr-only">{title}: amount</label>
        <input id={`${id}-n`} type="number" min={0} max={MAX[value.unit]} step={1} inputMode="numeric" value={value.n}
               onChange={(e) => onChange({ ...value, n: Math.max(0, Math.min(MAX[value.unit], Number(e.target.value) || 0)) })} />
        <label htmlFor={`${id}-unit`} className="sr-only">{title}: unit</label>
        <select id={`${id}-unit`} value={value.unit} onChange={(e) => {
          const unit = e.target.value as Unit;
          onChange({ ...value, unit, n: Math.min(value.n, MAX[unit]) });
        }}>
          <option value="days">days</option><option value="weeks">weeks</option><option value="months">months</option>
        </select>
        <div className="segmented small" role="radiogroup" aria-label={`${title}: direction`}>
          {(["earlier", "later"] as Dir[]).map((d) => (
            <button key={d} type="button" role="radio" aria-checked={value.dir === d} aria-selected={value.dir === d}
                    onClick={() => onChange({ ...value, dir: d })}>{d}</button>
          ))}
        </div>
      </div>
      <small className="meta">{hint}</small>
    </fieldset>
  );
}

/** Base vs scenario net gap per bucket, as paired columns around a zero line. */
function WhatIfChart({ rows, currency }: { rows: WhatIfBucket[]; currency: string }) {
  const W = 560, H = 230, top = 10, bottom = 44, left = 8;
  const max = Math.max(1, ...rows.flatMap((r) => [Math.abs(r.base_net), Math.abs(r.scen_net)]));
  const y0 = top + (H - top - bottom) / 2;
  const scale = (H - top - bottom) / 2 / max;
  const slot = (W - left * 2) / rows.length;
  const bw = Math.min(28, slot / 3.2);
  return (
    <figure className="whatif-chart">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Net gap per bucket, base vs scenario, ${currency}`}>
        <line x1={left} x2={W - left} y1={y0} y2={y0} className="axis" />
        {rows.map((r, i) => {
          const cx = left + slot * i + slot / 2;
          const bar = (v: number, x: number, cls: string, label: string) => {
            const h = Math.max(1, Math.abs(v) * scale);
            return <rect key={cls} x={x} y={v >= 0 ? y0 - h : y0} width={bw} height={h} rx={3} className={cls}><title>{label}: {formatMoney(v)} {currency}</title></rect>;
          };
          return (
            <g key={r.bucket}>
              {bar(r.base_net, cx - bw - 2, "base", `${r.bucket} base`)}
              {bar(r.scen_net, cx + 2, "scen", `${r.bucket} scenario`)}
              <text x={cx} y={H - bottom + 18} textAnchor="middle" className="tick">{r.bucket}</text>
              <text x={cx} y={H - bottom + 34} textAnchor="middle" className="tick mono" data-tone={r.delta < 0 ? "shortfall" : r.delta > 0 ? "surplus" : undefined}>
                {r.delta ? formatSignedCompact(r.delta) : "±0"}
              </text>
            </g>
          );
        })}
      </svg>
      <figcaption className="legend">
        <span><i className="key base" /> Base</span><span><i className="key scen" /> Scenario</span>
        <span className="meta">Figures under each bucket: change in net gap</span>
      </figcaption>
    </figure>
  );
}

/** What-if: shift asset and/or liability maturities and see the effect on the gap ladder. */
export function WhatIfPage() {
  const [base, setBase] = useSticky<WhatIfBase>("wi.base", "deterministic");
  const [asset, setAsset] = useSticky<Shift>("wi.asset", NONE);
  const [liability, setLiability] = useSticky<Shift>("wi.liability", { n: 1, unit: "months", dir: "earlier" });
  const [currency, setCurrency] = useSticky("wi.currency", "");
  const [location, setLocation] = useSticky("wi.location", "");
  const [data, setData] = useSticky<WhatIfResponse | null>("wi.data", null);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);
  const seq = useRef(0);

  const query = useMemo<WhatIfQuery>(() => {
    const a = toParams(asset), l = toParams(liability);
    return { base, currency: currency || undefined, location: location || undefined,
             asset_shift_months: a.months, asset_shift_days: a.days, liability_shift_months: l.months, liability_shift_days: l.days };
  }, [base, asset, liability, currency, location]);

  useEffect(() => {
    const mine = ++seq.current;
    const t = setTimeout(() => {                                // one request after the inputs stop changing
      setBusy(true); setError(null);
      getWhatIf(query).then((d) => {
        if (mine !== seq.current) return;
        setData(d);
        if (!currency) setCurrency(d.metadata.currency);
      }, (e: ApiError) => { if (mine === seq.current) setError(e); })
        .finally(() => { if (mine === seq.current) setBusy(false); });
    }, 250);
    return () => clearTimeout(t);
  }, [JSON.stringify(query)]);   // eslint-disable-line react-hooks/exhaustive-deps

  const md = data?.metadata, k = data?.kpis;
  const ccy = md?.currency ?? currency;

  return (
    <main className="page whatif-page">
      <div className="page-head">
        <div>
          <h1>What-if scenarios</h1>
          <p>Shift asset and/or liability maturities and see the effect on the gap ladder{md && <> · calculation date {md.calculation_date}</>}</p>
        </div>
        <div className="segmented" role="tablist" aria-label="Base ladder">
          <button type="button" role="tab" aria-selected={base === "deterministic"} onClick={() => setBase("deterministic")}>Contractual base</button>
          <button type="button" role="tab" aria-selected={base === "ai"} onClick={() => setBase("ai")}>AI-adjusted base</button>
        </div>
      </div>

      <section className="card whatif-controls" aria-label="Scenario">
        <div className="shift-grid">
          <ShiftControl id="liab" title="Liability maturities (deposits)" hint="Earlier = deposits leave sooner (outflow stress)." value={liability} onChange={setLiability} />
          <ShiftControl id="asset" title="Asset maturities (loans)" hint="Later = loan repayments arrive later (inflow delay)." value={asset} onChange={setAsset} />
          <div className="filters">
            <label>Currency
              <select value={ccy} onChange={(e) => { setCurrency(e.target.value); setLocation(""); }}>
                {(md?.currencies ?? (ccy ? [ccy] : [])).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </label>
            <label>Location
              <select value={location} onChange={(e) => setLocation(e.target.value)}>
                <option value="">All locations</option>
                {(md?.locations ?? []).map((l) => <option key={l} value={l}>{l}</option>)}
              </select>
            </label>
          </div>
        </div>
        <div className="presets" role="group" aria-label="Presets">
          {PRESETS.map((p) => (
            <button key={p.label} type="button" className="btn secondary" onClick={() => { setAsset(p.asset); setLiability(p.liability); }}>{p.label}</button>
          ))}
        </div>
      </section>

      {error && <div className="banner" role="alert">{error.message}</div>}
      {!data && !error && <p className="loading" aria-busy="true">Running the scenario…</p>}

      {data && md && k && (
        <div aria-busy={busy} className={`whatif-results${busy ? " stale" : ""}`}>
          <p className="scenario-line" role="status"><b>{md.scenario}</b> · {md.base === "ai" ? "AI-adjusted" : "contractual"} base · {ccy}{md.location ? ` · ${md.location}` : ""}</p>
          <section className="kpi-row" aria-label="Key figures">
            <div className="kpi"><span>Contracts changing bucket</span><b>{formatInt(k.moved)} / {formatInt(k.total)}</b><small>{formatInt(k.shifted)} dates moved</small></div>
            <div className="kpi"><span>Pulled to today</span><b>{formatInt(k.pulled_to_today)}</b><small>shifted before the calc date → 0-1M</small></div>
            <div className="kpi"><span>Worst bucket · base</span><b className={k.worst_base.net < 0 ? "neg" : "pos"}>{formatSignedCompact(k.worst_base.net)}</b><small>{k.worst_base.bucket}</small></div>
            <div className="kpi"><span>Worst bucket · scenario</span><b className={k.worst_scenario.net < 0 ? "neg" : "pos"}>{formatSignedCompact(k.worst_scenario.net)}</b><small>{k.worst_scenario.bucket} · total {formatSignedCompact(k.total_net)} unchanged</small></div>
          </section>

          <div className="whatif-grid">
            <section className="card" aria-label="Net gap chart">
              <h2>Net gap per bucket</h2>
              <WhatIfChart rows={data.buckets} currency={ccy} />
            </section>
            <section className="card" aria-label="Bucket table">
              <h2>Impact by bucket</h2>
              <div className="table-wrap">
                <table className="compact">
                  <thead><tr><th>Bucket</th><th className="num">Base</th><th className="num">Scenario</th><th className="num">Change</th><th className="num" title="Cumulative net gap">Cum.</th></tr></thead>
                  <tbody>{data.buckets.map((b) => (
                    <tr key={b.bucket}>
                      <td>{b.bucket}</td>
                      <td className="num mono">{formatSignedCompact(b.base_net)}</td>
                      <td className="num mono">{formatSignedCompact(b.scen_net)}</td>
                      <td className="num mono" data-tone={b.delta < 0 ? "shortfall" : b.delta > 0 ? "surplus" : undefined}>{b.delta ? formatSignedCompact(b.delta) : "—"}</td>
                      <td className="num mono" data-tone={b.scen_cum < 0 ? "shortfall" : undefined}>{formatSignedCompact(b.scen_cum)}</td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            </section>
          </div>

          <section className="card" aria-label="Recommended actions for this scenario">
            <h2>Recommended actions · scenario</h2>
            <Actions items={data.recommendations} empty="No action needed in this scenario." />
          </section>

          <section className="card" aria-label="Contracts that change bucket">
            <h2>Contracts that change bucket</h2>
            <p className="hint">{data.movers_total > data.movers.length ? `Largest ${data.movers.length} of ${formatInt(data.movers_total)}` : `${formatInt(data.movers_total)} contracts`}, by cash at maturity</p>
            {data.movers.length ? (
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Transaction</th><th>Type</th><th>Location</th><th className="num">Cash at maturity</th><th>Base date</th><th>Scenario date</th><th>Bucket</th></tr></thead>
                  <tbody>{data.movers.map((t) => (
                    <tr key={t.transaction_id}>
                      <td className="mono">{t.transaction_id}</td>
                      <td>{t.book === "asset" ? "Asset" : "Liability"}</td>
                      <td>{t.location}</td>
                      <td className="num mono">{formatMoney(t.cash_at_maturity)}</td>
                      <td className="mono">{t.base_date}</td>
                      <td className="mono">{t.scenario_date}{t.pulled_to_today ? " (due now)" : ""}</td>
                      <td>{t.base_bucket} → <b>{t.scenario_bucket}</b></td>
                    </tr>
                  ))}</tbody>
                </table>
              </div>
            ) : <p className="empty">No contract changes bucket in this scenario.</p>}
            <ul className="meta notes">{data.notes.map((n) => <li key={n}>{n}</li>)}</ul>
          </section>
        </div>
      )}
    </main>
  );
}
