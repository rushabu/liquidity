import { useId, useState } from "react";
import type { BucketKey, Recommendation } from "../api/client";
import { formatMoney } from "../lib/format";

const SEVERITY = { high: "error", medium: "warning", low: "success" } as const;
const PRIORITY = { high: "Act now", medium: "Plan", low: "Optional" } as const;
export const KIND: Record<Recommendation["kind"], string> = {
  fund: "Fund", cover: "Roll over", retain: "Retain", buffer: "Standby", keep_liquid: "Invest short", invest: "Invest",
};

const fmtDate = (d: string | null | undefined) =>
  d ? new Date(`${d}T00:00:00`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "open";

function figure(label: string, v: number, ccy: string) {
  if (/chance/i.test(label)) return `${v.toFixed(0)}%`;
  return `${v < 0 ? "−" : ""}${ccy} ${formatMoney(Math.abs(v))}`;
}

/** One action as a business owner reads it: what to do (one line), how urgent, how much, by when; hover, focus
 *  or tap opens "Where this comes from": the bucket window, the rule that fired and the exact figures. */
function ActionCard({ r, compact, onDrill, open, setOpen }: {
  r: Recommendation; compact: boolean; onDrill?: (bucket: BucketKey, currency: string) => void;
  open: boolean; setOpen: (o: boolean) => void;
}) {
  const id = useId();
  const ev = r.evidence;
  return (
    <li className="action" data-open={open ? "" : undefined}
        onPointerEnter={(e) => { if (e.pointerType !== "touch" && e.pointerType !== "pen") setOpen(true); }}
        onPointerLeave={(e) => { if (e.pointerType !== "touch" && e.pointerType !== "pen") setOpen(false); }}>
      <div className="action-side">
        <span className="chip" data-tone={SEVERITY[r.severity]}>{KIND[r.kind] ?? r.kind}</span>
        <small className="prio" data-tone={SEVERITY[r.severity]}>{PRIORITY[r.severity]}</small>
      </div>
      <div className="action-main">
        <b className="action-title">{r.action}</b>
        <span className="meta">{r.scope} · bucket {r.bucket}{ev?.bucket_start ? ` (${fmtDate(ev.bucket_start)} → ${fmtDate(ev.bucket_end)})` : ""}</span>
        {!compact && <p>{r.rationale}</p>}
      </div>
      {ev && (
        <button type="button" className="why-btn" aria-expanded={open} aria-controls={id}
                onClick={() => setOpen(true)} onBlur={() => setOpen(false)}
                onKeyDown={(e) => { if (e.key === "Escape") setOpen(false); }}>
          Where from?
        </button>
      )}
      {ev && open && (
        <div className="evidence" id={id} role="tooltip">
          <b>Where this comes from</b>
          <p className="ev-window">Bucket <b>{r.bucket}</b> · {fmtDate(ev.bucket_start)} → {fmtDate(ev.bucket_end)} · {r.currency}{r.location ? ` · ${r.location}` : ""}</p>
          <p className="ev-rule">{ev.rule}</p>
          <dl>
            {ev.figures.map((f) => (
              <div key={f.label} data-key={/still to raise|standby needed|extra outflow|left to invest|unused|rolled into/i.test(f.label) ? "" : undefined}>
                <dt>{f.label}</dt><dd data-tone={f.value < 0 ? "shortfall" : undefined}>{figure(f.label, f.value, r.currency)}</dd>
              </div>
            ))}
          </dl>
          {ev.sources.length > 0 && <p className="meta">Cash comes from: {ev.sources.join(", ")}</p>}
          {onDrill && <p className="meta">Click the bucket in the table to see the contracts behind it.</p>}
        </div>
      )}
    </li>
  );
}

export function Actions({ items, empty, compact = false, onDrill }: {
  items: Recommendation[]; empty: string; compact?: boolean; onDrill?: (bucket: BucketKey, currency: string) => void;
}) {
  const [openKey, setOpenKey] = useState<string | null>(null);      // one evidence card open at a time
  if (!items.length) return <p className="empty">{empty}</p>;
  const total = (sev: Recommendation["severity"]) => items.filter((r) => r.severity === sev).length;
  return (
    <>
      {!compact && (
        <p className="actions-summary">
          <span className="chip" data-tone="error">{total("high")} act now</span>
          <span className="chip" data-tone="warning">{total("medium")} plan</span>
          <span className="chip" data-tone="success">{total("low")} optional</span>
          <span className="meta">Hover or tap “Where from?” on an action to see the figures behind it.</span>
        </p>
      )}
      <ol className="actions">
        {items.map((r) => {
          const k = `${r.scope}-${r.bucket}-${r.kind}-${r.action}`;
          return <ActionCard key={k} r={r} compact={compact} onDrill={onDrill} open={openKey === k}
                             setOpen={(o) => setOpenKey((cur) => (o ? k : cur === k ? null : cur))} />;
        })}
      </ol>
    </>
  );
}
