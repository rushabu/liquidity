import { useEffect, useRef, useState } from "react";
import type { BucketKey, LadderRow } from "../api/client";
import { formatCompact, formatInt, formatMoney } from "../lib/format";

/** Assets vs liabilities per maturity bucket (side-by-side columns on one axis), with the net gap
 *  under each bucket. Pure SVG drawn at the container's real pixel width; exact figures on hover. */

const H = 300;
const M = { top: 16, right: 12, left: 64 };
const BOTTOM = { inline: 58, stacked: 72 };                  // bucket name + net gap row (two lines when narrow)
const STACK_BELOW = 84;                                      // slot width (px) under which "▲ +140.6M" no longer fits
const BAR_MAX = 24;
const BAR_GAP = 2;
const TIP_W = 300;

/** Clean axis figures: 0, 250M, 500M, 1.5B. */
const axisLabel = (n: number): string => {
  const [d, u] = n >= 1e9 ? [1e9, "B"] : n >= 1e6 ? [1e6, "M"] : n >= 1e3 ? [1e3, "K"] : [1, ""];
  return `${+(n / d).toFixed(2)}${u}`;
};

function niceTicks(max: number, count = 4): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  return Array.from({ length: Math.ceil(max / step - 1e-9) + 1 }, (_, i) => i * step);
}

/** Column with a 4px rounded data end and a square foot on the baseline. */
const column = (x: number, y: number, w: number, h: number): string => {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
};

const signed = (n: number) => (n > 0 ? `+${formatCompact(n)}` : formatCompact(n));
const signedMoney = (n: number) => (n > 0 ? `+${formatMoney(n)}` : formatMoney(n));
const ARROW = { surplus: "▲", shortfall: "▼", balanced: "●" } as const;

function useWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(300, Math.round(e.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

export function GapChart({ rows, currency, scope, onSelect }: {
  rows: LadderRow[];
  currency: string;
  scope?: string;
  onSelect?: (bucket: BucketKey) => void;
}) {
  const [ref, W] = useWidth(720);
  const [hover, setHover] = useState<number | null>(null);

  const plotW = W - M.left - M.right;
  const slot = plotW / Math.max(1, rows.length);
  const stacked = slot < STACK_BELOW;
  const plotH = H - M.top - (stacked ? BOTTOM.stacked : BOTTOM.inline);
  const ticks = niceTicks(Math.max(0, ...rows.flatMap((r) => [r.asset_cash_at_maturity, r.liability_cash_at_maturity])));
  const yMax = ticks[ticks.length - 1];
  const y = (v: number) => M.top + plotH - (v / yMax) * plotH;
  const base = y(0);
  const bw = Math.max(6, Math.min(BAR_MAX, Math.floor(slot * 0.3)));
  const summary = rows.map((r) => `${r.bucket}: assets ${formatCompact(r.asset_cash_at_maturity)}, liabilities `
    + `${formatCompact(r.liability_cash_at_maturity)}, net ${r.gap_status} ${formatCompact(Math.abs(r.net_gap))}`).join("; ");

  const h = hover === null ? null : rows[hover];
  const hx = hover === null ? 0 : M.left + slot * (hover + 0.5);
  const tipLeft = hx + slot / 2 - 4 + TIP_W <= W ? hx + slot / 2 - 4 : Math.max(0, hx - slot / 2 + 4 - TIP_W);

  return (
    <figure className="gapchart">
      <figcaption className="gapchart-head">
        <div>
          <h2>Assets vs liabilities by maturity bucket</h2>
          <p>Cash at maturity (principal + interest) in {currency}{scope ? `, ${scope}` : ""}. Net gap = assets − liabilities.</p>
        </div>
        <ul className="gapchart-legend">
          <li><span className="key" style={{ background: "var(--series-asset)" }} />Assets <small>cash in</small></li>
          <li><span className="key" style={{ background: "var(--series-liability)" }} />Liabilities <small>cash out</small></li>
          <li><span className="tone" data-tone="surplus">▲</span>surplus <span className="tone" data-tone="shortfall">▼</span>shortfall</li>
        </ul>
      </figcaption>

      <div className="gapchart-plot" ref={ref} onPointerLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Assets and liabilities by bucket in ${currency}. ${summary}`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--axis)" : "var(--grid)"} />
              <text x={M.left - 10} y={y(t)} dy="0.32em" textAnchor="end" className="gapchart-tick">{axisLabel(t)}</text>
            </g>
          ))}
          <text x={M.left - 10} y={H - 12} textAnchor="end" className="gapchart-rowlabel">Net gap</text>

          {rows.map((r, i) => {
            const cx = M.left + slot * (i + 0.5);
            const ha = r.asset_cash_at_maturity > 0 ? Math.max(1, base - y(r.asset_cash_at_maturity)) : 0;
            const hl = r.liability_cash_at_maturity > 0 ? Math.max(1, base - y(r.liability_cash_at_maturity)) : 0;
            return (
              <g key={r.bucket}>
                {hover === i && <rect className="gapchart-band" x={cx - slot / 2 + 4} y={M.top - 6} width={slot - 8} height={H - M.top} rx={8} />}
                {ha > 0 && <path d={column(cx - BAR_GAP / 2 - bw, base - ha, bw, ha)} fill="var(--series-asset)" />}
                {hl > 0 && <path d={column(cx + BAR_GAP / 2, base - hl, bw, hl)} fill="var(--series-liability)" />}
                <text x={cx} y={base + 20} textAnchor="middle" className="gapchart-cat">{r.bucket}</text>
                {stacked ? (
                  <text textAnchor="middle" className="gapchart-net" data-stacked="">
                    <tspan x={cx} y={H - 28} className="tone" data-tone={r.gap_status}>{ARROW[r.gap_status]}</tspan>
                    <tspan x={cx} y={H - 12}>{signed(r.net_gap)}</tspan>
                  </text>
                ) : (
                  <text x={cx} y={H - 12} textAnchor="middle" className="gapchart-net">
                    <tspan className="tone" data-tone={r.gap_status}>{ARROW[r.gap_status]}</tspan> {signed(r.net_gap)}
                  </text>
                )}
                <rect className="gapchart-hit" data-clickable={onSelect ? "" : undefined} x={cx - slot / 2} y={0} width={slot} height={H}
                      onPointerEnter={() => setHover(i)} onClick={onSelect && (() => onSelect(r.bucket))} />
              </g>
            );
          })}
        </svg>

        {h && (
          <div className="gapchart-tip" style={{ left: tipLeft, top: M.top, width: TIP_W }}>
            <div className="tip-title"><b>{h.bucket_label}</b><span>{h.start_date} → {h.end_date ?? "open"}</span></div>
            <div className="tip-row"><i style={{ background: "var(--series-asset)" }} /><b>{formatMoney(h.asset_cash_at_maturity)}</b><span>Assets · {formatInt(h.asset_count)} txns</span></div>
            <div className="tip-row"><i style={{ background: "var(--series-liability)" }} /><b>{formatMoney(h.liability_cash_at_maturity)}</b><span>Liabilities · {formatInt(h.liability_count)} txns</span></div>
            <div className="tip-row net"><span className="tone" data-tone={h.gap_status}>{ARROW[h.gap_status]}</span><b>{signedMoney(h.net_gap)}</b><span>Net gap · {h.gap_status}</span></div>
            {onSelect && <div className="tip-hint">Click to see the transactions</div>}
          </div>
        )}
      </div>
    </figure>
  );
}
