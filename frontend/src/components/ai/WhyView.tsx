import { useEffect, useRef, useState } from "react";
import { getWhy, type ApiError, type WhyResponse } from "../../api/client";
import { formatInt, formatPct, formatSignedCompact } from "../../lib/format";
import { ModelCardPanel } from "../ModelCardPanel";
import type { DrillPreset, PageMeta, Shared } from "./types";

const SHADES = ["var(--ai-1)", "var(--ai-2)", "var(--ai-3)", "var(--ai-4)"];

/** Bold the "Mostly <segment> <loans|deposits>" phrase and the market wording, like the design. */
function Story({ text }: { text: string }) {
  const parts = text.split(/(Mostly [A-Za-z]+ (?:loans|deposits)|above market|below market)/);
  return (
    <>
      {parts.map((p, i) => {
        if (i % 2 === 0) return p;
        return p.startsWith("Mostly ") ? <span key={i}>Mostly <b>{p.slice(7)}</b></span> : <b key={i}>{p}</b>;
      })}
    </>
  );
}

export function WhyView({ shared, onDrill, onMeta }: { shared: Shared; onDrill: (p: DrillPreset) => void; onMeta: (m: PageMeta) => void }) {
  const { currency, location } = shared;
  const [shift, setShift] = useState(0);
  const [applied, setApplied] = useState(0);
  const [data, setData] = useState<WhyResponse | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [aboutOpen, setAboutOpen] = useState(false);
  const seq = useRef(0);

  useEffect(() => {                                     // debounce the slider: one request after it stops moving
    const t = setTimeout(() => setApplied(shift), 250);
    return () => clearTimeout(t);
  }, [shift]);
  useEffect(() => {
    const mine = ++seq.current;
    setError(null);
    getWhy({ currency: currency || undefined, location: location || undefined, market_shift_pp: applied })
      .then((d) => { if (mine === seq.current) { setData(d); onMeta(d.metadata); } }, (e: ApiError) => { if (mine === seq.current) setError(e); });
  }, [currency, location, applied]);

  if (error) return <div className="banner" role="alert">{error.message}</div>;
  if (!data) return <p className="loading">Working out why each bucket moved…</p>;
  const sc = data.scenario;
  const ccy = data.metadata.currency;
  const loanArrow = sc.loans_repaid_early.change > 0 ? "↑" : sc.loans_repaid_early.change < 0 ? "↓" : "";
  const depArrow = sc.deposits_withdrawn_early.change > 0 ? "↑" : sc.deposits_withdrawn_early.change < 0 ? "↓" : "";

  return (
    <>
      <div className="ai-grid">
        <section className="card" aria-label="Why each bucket moved">
          <h2>Why each bucket moved</h2>
          <p className="hint">{ccy}{location ? ` · ${location}` : ""} · click a bucket to drill into its contracts</p>
          <ul className="why-list">
            {data.buckets.map((b) => (
              <li key={b.bucket}>
                <button type="button" className="link-btn ai bucket-link" onClick={() => onDrill({ bucket: b.bucket, currency: ccy, moved_only: b.moved_in > 0 })}>{b.bucket}</button>
                <p><Story text={b.text} />{b.largest && <b> Largest change.</b>}</p>
                <span className="mono shift" data-tone={b.shift < 0 ? "shortfall" : b.shift > 0 ? "surplus" : undefined}>{formatSignedCompact(b.shift)}</span>
              </li>
            ))}
          </ul>
        </section>

        <div className="stack">
          <section className="card" aria-label="What drives the AI">
            <h2>What drives the AI</h2>
            <ul className="importance">
              {data.drivers.map((d) => (
                <li key={d.label}>
                  <span>{d.label}</span>
                  <span className="imp-bar" aria-hidden="true"><i style={{ width: `${Math.max(2, d.strength * 100)}%` }} /></span>
                  <b>{d.level}</b>
                </li>
              ))}
            </ul>
          </section>

          <section className="card" aria-label="Market-rate scenario">
            <h2>Market-rate scenario</h2>
            <p className="hint">Spread = contract rate − market rate</p>
            <div className="slider-head">
              <label htmlFor="mshift">Market rate shift</label>
              <b className="mono ai-text">{shift > 0 ? "+" : shift < 0 ? "−" : ""}{Math.abs(shift).toFixed(1)} pp</b>
            </div>
            <input id="mshift" type="range" min={-2} max={2} step={0.1} value={shift} onChange={(e) => setShift(Number(e.target.value))}
                   aria-valuetext={`${shift.toFixed(1)} percentage points`} className="ai-range" />
            <div className="range-scale"><span>−2.0</span><span>0</span><span>+2.0 pp</span></div>
            <dl className="scenario">
              <div><dt>Loans likely repaid early</dt><dd>{loanArrow} {sc.loans_repaid_early.change > 0 ? "+" : ""}{formatInt(sc.loans_repaid_early.change)}</dd></div>
              <div><dt>Deposits likely withdrawn early</dt><dd>{depArrow} {sc.deposits_withdrawn_early.change > 0 ? "+" : ""}{formatInt(sc.deposits_withdrawn_early.change)}</dd></div>
              <div><dt>{sc.headline.bucket} gap</dt><dd className={sc.headline.scenario < 0 ? "neg" : "pos"}>{formatSignedCompact(sc.headline.base)} → {formatSignedCompact(sc.headline.scenario)}</dd></div>
            </dl>
            {applied !== shift && <p className="meta">Updating…</p>}
            <p className="meta">{sc.note} Expected counts from {formatInt(Math.round(sc.loans_repaid_early.base))} loans and {formatInt(Math.round(sc.deposits_withdrawn_early.base))} deposits today.</p>
          </section>
        </div>
      </div>

      <section className="card" aria-label="Who moved">
        <h2>Who moved</h2>
        <p className="hint">{formatInt(data.who_moved.total)} contracts changed bucket, by segment and side</p>
        {data.who_moved.total > 0 ? (
          <>
            <div className="stackbar" role="img" aria-label={data.who_moved.groups.map((g) => `${g.label} ${formatPct(g.share)}`).join(", ")}>
              {data.who_moved.groups.map((g, i) => (
                <i key={g.label} style={{ width: `${g.share * 100}%`, background: SHADES[i % SHADES.length] }} title={`${g.label}: ${g.count}`} />
              ))}
            </div>
            <ul className="stack-legend">
              {data.who_moved.groups.map((g, i) => (
                <li key={g.label}><span className="key" style={{ background: SHADES[i % SHADES.length] }} />{g.label} {formatPct(g.share)}</li>
              ))}
            </ul>
          </>
        ) : <p className="empty">No contract changes bucket for this selection.</p>}
      </section>

      <details className="card" onToggle={(e) => setAboutOpen((e.target as HTMLDetailsElement).open)}>
        <summary><b>About the model</b> <span className="meta">accuracy, F1, calibration, drivers, timing, assumptions, full prediction export</span></summary>
        {aboutOpen && <ModelCardPanel />}
      </details>
    </>
  );
}
