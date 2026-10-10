import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { getLadder, type ApiError, type BucketKey, type LadderResponse, type LadderRow, type PastDuePolicy } from "../api/client";
import { DrillDown, type Cell } from "../components/DrillDown";
import { ExplainPanel } from "../components/ExplainPanel";
import { GapChart } from "../components/GapChart";
import { isValidIsoDate } from "../lib/dates";
import { formatInt, formatMoney } from "../lib/format";

const ALL = "__all__";

export function LadderPage() {
  const [calcDate, setCalcDate] = useState("");
  const [policy, setPolicy] = useState<PastDuePolicy>("exclude");
  const [data, setData] = useState<LadderResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [currency, setCurrency] = useState("");
  const [location, setLocation] = useState(ALL);
  const [cell, setCell] = useState<Cell | null>(null);
  const [explainId, setExplainId] = useState<string | null>(null);
  const seq = useRef(0);

  const dateOk = calcDate === "" || isValidIsoDate(calcDate);
  const query = useMemo(() => ({ calc_date: calcDate || undefined, past_due_policy: policy }), [calcDate, policy]);

  useEffect(() => {
    if (!dateOk) return;
    const mine = ++seq.current;            // a slower, older response must not overwrite a newer one
    setError(null);
    getLadder(query).then((d) => {
      if (mine !== seq.current) return;
      setData(d);
      setCurrency((c) => (c && d.ladder.some((r) => r.currency === c) ? c : d.ladder[0]?.currency ?? ""));
    }, (e: ApiError) => { if (mine === seq.current) setError(e); });
    setCell(null); setExplainId(null);
  }, [query, dateOk]);

  const currencies = useMemo(() => [...new Set((data?.ladder ?? []).map((r) => r.currency))], [data]);
  const locations = useMemo(
    () => [...new Set((data?.ladder ?? []).filter((r) => r.currency === currency && r.location).map((r) => r.location as string))].sort(),
    [data, currency],
  );
  const rows: LadderRow[] = useMemo(
    () => (data?.ladder ?? []).filter((r) => r.currency === currency && (location === ALL ? r.scope === "currency_total" : r.location === location)),
    [data, currency, location],
  );
  const pick = (bucket: BucketKey) => { setExplainId(null); setCell({ currency, location: location === ALL ? null : location, bucket }); };
  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.net_gap)));

  return (
    <main className="page">
      <div className="page-head">
        <div>
          <h1>Deterministic gap ladder</h1>
          <p>Contractual cash flows (simple interest, Actual/365) placed in maturity buckets. Net gap = assets − liabilities. Currencies are never combined.</p>
        </div>
        <div className="datebox">
          <label htmlFor="calc">Calculation date (optional)</label>
          <input id="calc" value={calcDate} placeholder={data?.metadata.as_of_date ?? "YYYY-MM-DD"} onChange={(e) => setCalcDate(e.target.value.trim())} aria-invalid={!dateOk} />
          {!dateOk ? <span className="err">Use a real date as YYYY-MM-DD</span> : <span className="help">Default: the snapshot date</span>}
        </div>
      </div>

      {error && (
        <div className="banner" role="alert">
          {error.message}
          {error.code === "SNAPSHOT_NOT_READY" && <> <Link to="/upload">Go to upload</Link></>}
        </div>
      )}
      {!data && !error && <p className="loading">Loading ladder…</p>}

      {data && (
        <>
          <section className="card strip" aria-label="Snapshot">
            <div>
              <div className="big">Calculation date <b>{data.metadata.calculation_date}</b></div>
              <div className="sub">
                {formatInt(data.metadata.transactions_in_ladder)} of {formatInt(data.metadata.transactions_read)} transactions in the ladder · {data.metadata.asset_batch.filename} + {data.metadata.liability_batch.filename}
                {data.metadata.scope_location && <> · limited to {data.metadata.scope_location}</>}
              </div>
            </div>
            <div className="filters">
              <label>Past-due contracts
                <select value={policy} onChange={(e) => setPolicy(e.target.value as PastDuePolicy)}>
                  <option value="exclude">Exclude and list</option>
                  <option value="bucket_0_1m">Count in 0-1M</option>
                </select>
              </label>
              {data.metadata.past_due_count > 0 && (
                <button type="button" className="btn secondary" onClick={() => { setExplainId(null); setCell({ currency, location: null, bucket: "past_due" }); }}>
                  {formatInt(data.metadata.past_due_count)} past due
                </button>
              )}
            </div>
          </section>
          {data.warnings.map((w) => <p key={w} className="note">{w}</p>)}

          <section className="card" aria-label="Ladder">
            <div className="table-head">
              <div role="tablist" aria-label="Currency" className="filters">
                {currencies.map((c) => (
                  <button key={c} role="tab" type="button" aria-selected={c === currency} className={c === currency ? "btn" : "btn secondary"}
                          onClick={() => { setCurrency(c); setLocation(ALL); setCell(null); setExplainId(null); }}>{c}</button>
                ))}
              </div>
              <div className="filters">
                <label>Location
                  <select value={location} onChange={(e) => { setLocation(e.target.value); setCell(null); setExplainId(null); }}>
                    <option value={ALL}>All locations{data.metadata.scope_location ? "" : " (roll-up)"}</option>
                    {locations.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                </label>
              </div>
            </div>

            <GapChart rows={rows} currency={currency} onSelect={pick}
                      scope={location === ALL ? data.metadata.scope_location ?? "all locations" : location} />

            <div className="table-wrap">
              <table>
                <thead><tr><th>Bucket</th><th>Window</th><th className="num">Assets ({currency})</th><th className="num">Liabilities ({currency})</th><th className="num">Net gap</th><th>Status</th><th className="num">Txns</th></tr></thead>
                <tbody>
                  {rows.map((r) => {
                    const heat = Math.min(0.28, (Math.abs(r.net_gap) / maxAbs) * 0.28);
                    const bg = r.net_gap === 0 ? undefined : r.net_gap > 0 ? `rgba(18,113,91,${heat})` : `rgba(180,35,24,${heat})`;
                    return (
                      <tr key={r.bucket}>
                        <td><button type="button" className="link-btn" onClick={() => pick(r.bucket)} aria-label={`Show ${r.bucket} transactions`}>{r.bucket}</button></td>
                        <td className="file">{r.start_date} → {r.end_date ?? "open"}</td>
                        <td className="num mono">{formatMoney(r.asset_cash_at_maturity)}</td>
                        <td className="num mono">{formatMoney(r.liability_cash_at_maturity)}</td>
                        <td className="num mono" style={{ background: bg, fontWeight: 600 }}>{formatMoney(r.net_gap)}</td>
                        <td><span className="chip" data-tone={r.gap_status === "surplus" ? "success" : r.gap_status === "shortfall" ? "error" : "neutral"}>{r.gap_status}</span></td>
                        <td className="num mono">{formatInt(r.asset_count + r.liability_count)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <p className="meta">Each figure is the sum of cash at maturity (principal + interest). Click a bucket to see the exact transactions behind it.</p>
          </section>

          {cell && <DrillDown cell={cell} query={query} onExplain={setExplainId} onClose={() => { setCell(null); setExplainId(null); }} />}
          {explainId && <ExplainPanel id={explainId} query={query} onClose={() => setExplainId(null)} />}

          <details className="card">
            <summary><b>Assumptions used</b></summary>
            <ul>{data.assumptions.map((a) => <li key={a}>{a}</li>)}</ul>
          </details>
        </>
      )}
    </main>
  );
}
