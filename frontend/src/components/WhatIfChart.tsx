import { useState } from "react";
import type { WhatIfBucket } from "../api/client";
import { formatCompact, formatInt, formatMoney, formatSignedCompact } from "../lib/format";
import { axisLabel, column, useWidth } from "./GapChart";

/** Net gap per bucket, contractual base vs what-if scenario: same look and behaviour as the deterministic
 *  ladder chart (GapChart): value axis with gridlines, rounded columns, hover band + tooltip. Net gaps can be
 *  negative, so the axis spans below zero; the row under the axis is the CHANGE (scenario − base). */

const H = 330;
const M = { top: 16, right: 12, left: 64 };
const BELOW = { inline: 62, stacked: 78 };         // bucket name + change row under the plot (two lines when narrow)
const STACK_BELOW = 84;                            // slot width (px) under which "▲ +220.4M" no longer fits
const BAR_MAX = 26;
const BAR_GAP = 3;
const TIP_W = 300;

/** Clean ticks spanning [lo, hi] (lo ≤ 0 ≤ hi), e.g. −600M … 200M in 200M steps. */
export function signedTicks(lo: number, hi: number, count = 5): number[] {
  const span = Math.max(hi - lo, 1);
  const raw = span / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const from = Math.floor(lo / step + 1e-9), to = Math.ceil(hi / step - 1e-9);
  return Array.from({ length: to - from + 1 }, (_, i) => (from + i) * step);
}

/** Rounded data end for negative columns (mirror of `column`). */
const columnDown = (x: number, y: number, w: number, h: number): string => {
  const r = Math.min(4, w / 2, h);
  return `M${x},${y}V${y + h - r}Q${x},${y + h} ${x + r},${y + h}H${x + w - r}Q${x + w},${y + h} ${x + w},${y + h - r}V${y}Z`;
};

/** In words, what the shift does to the bucket's net gap. */
export function changeText(base: number, scen: number): string {
  const d = scen - base, amt = formatCompact(Math.abs(d));
  if (Math.abs(d) < 0.5) return "No change";
  if (d > 0) {
    if (base < 0) return scen < 0 ? `Shortfall shrinks by ${amt}` : `Shortfall turns into a surplus`;
    return `Surplus grows by ${amt}`;
  }
  if (base > 0) return scen >= 0 ? `Surplus shrinks by ${amt}` : `Surplus turns into a shortfall`;
  return `Shortfall deepens by ${amt}`;
}

const money = (v: number) => (v > 0 ? `+${formatMoney(v)}` : v < 0 ? `−${formatMoney(-v)}` : formatMoney(0));
const tone = (v: number) => (v > 0 ? "surplus" : v < 0 ? "shortfall" : "balanced");

export function WhatIfChart({ rows, currency, scope }: { rows: WhatIfBucket[]; currency: string; scope?: string }) {
  const [ref, W] = useWidth(720);
  const [hover, setHover] = useState<number | null>(null);

  const values = rows.flatMap((r) => [r.base_net, r.scen_net]);
  const ticks = signedTicks(Math.min(0, ...values), Math.max(0, ...values));
  const lo = ticks[0], hi = ticks[ticks.length - 1];
  const plotW = W - M.left - M.right;
  const slot = plotW / Math.max(1, rows.length);
  const stacked = slot < STACK_BELOW;
  const plotH = H - M.top - (stacked ? BELOW.stacked : BELOW.inline);
  const y = (v: number) => M.top + ((hi - v) / (hi - lo || 1)) * plotH;
  const zero = y(0);
  const bw = Math.max(6, Math.min(BAR_MAX, Math.floor(slot * 0.28)));
  const bottom = M.top + plotH;

  const bar = (v: number, x: number, fill: string) => {
    if (!v) return null;
    const h = Math.max(1, Math.abs(y(v) - zero));
    return <path d={v > 0 ? column(x, zero - h, bw, h) : columnDown(x, zero, bw, h)} fill={fill} />;
  };

  const h = hover === null ? null : rows[hover];
  const hx = hover === null ? 0 : M.left + slot * (hover + 0.5);
  const tipLeft = hx + slot / 2 - 4 + TIP_W <= W ? hx + slot / 2 - 4 : Math.max(0, hx - slot / 2 + 4 - TIP_W);
  const summary = rows.map((r) => `${r.bucket}: base ${formatSignedCompact(r.base_net)}, scenario ${formatSignedCompact(r.scen_net)}, `
    + changeText(r.base_net, r.scen_net)).join("; ");

  return (
    <figure className="gapchart whatif-gapchart">
      <figcaption className="gapchart-head">
        <div>
          <h2>Net gap per bucket · contractual vs scenario</h2>
          <p>Net gap (assets − liabilities, cash at maturity) in {currency}{scope ? `, ${scope}` : ""}.</p>
        </div>
        <ul className="gapchart-legend">
          <li><span className="key" style={{ background: "var(--series-det)" }} />Contractual <small>base</small></li>
          <li><span className="key" style={{ background: "var(--primary)" }} />Scenario <small>shifted</small></li>
          <li><span className="tone" data-tone="surplus">▲</span>better <span className="tone" data-tone="shortfall">▼</span>worse</li>
        </ul>
      </figcaption>

      <div className="gapchart-plot" ref={ref} onPointerLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Net gap per bucket in ${currency}, contractual vs scenario. ${summary}`}>
          {ticks.map((t) => (
            <g key={t}>
              <line x1={M.left} x2={W - M.right} y1={y(t)} y2={y(t)} stroke={t === 0 ? "var(--axis)" : "var(--grid)"} strokeWidth={t === 0 ? 1.5 : 1} />
              <text x={M.left - 10} y={y(t)} dy="0.32em" textAnchor="end" className="gapchart-tick">{t < 0 ? `−${axisLabel(-t)}` : axisLabel(t)}</text>
            </g>
          ))}
          <text x={M.left - 10} y={bottom + (stacked ? 56 : 44)} textAnchor="end" className="gapchart-rowlabel">Change</text>

          {rows.map((r, i) => {
            const cx = M.left + slot * (i + 0.5);
            return (
              <g key={r.bucket}>
                {hover === i && <rect className="gapchart-band" x={cx - slot / 2 + 4} y={M.top - 6} width={slot - 8} height={H - M.top} rx={8} />}
                {bar(r.base_net, cx - BAR_GAP / 2 - bw, "var(--series-det)")}
                {bar(r.scen_net, cx + BAR_GAP / 2, "var(--primary)")}
                <text x={cx} y={bottom + 22} textAnchor="middle" className="gapchart-cat">{r.bucket}</text>
                {stacked ? (
                  <text textAnchor="middle" className="gapchart-net" data-stacked="">
                    <tspan x={cx} y={bottom + 40} className="tone" data-tone={tone(r.delta)}>{r.delta > 0 ? "▲" : r.delta < 0 ? "▼" : "●"}</tspan>
                    <tspan x={cx} y={bottom + 56}>{r.delta ? formatSignedCompact(r.delta) : "±0"}</tspan>
                  </text>
                ) : (
                  <text x={cx} y={bottom + 44} textAnchor="middle" className="gapchart-net">
                    {r.delta ? <><tspan className="tone" data-tone={tone(r.delta)}>{r.delta > 0 ? "▲" : "▼"}</tspan> {formatSignedCompact(r.delta)}</> : "±0"}
                  </text>
                )}
                <rect className="gapchart-hit" x={cx - slot / 2} y={0} width={slot} height={H}
                      onPointerEnter={() => setHover(i)} onClick={() => setHover(i)} />
              </g>
            );
          })}
        </svg>

        {h && (
          <div className="gapchart-tip" style={{ left: tipLeft, top: M.top, width: TIP_W }} role="status">
            <div className="tip-title"><b>{h.label}</b><span>{h.start_date} → {h.end_date ?? "open"}</span></div>
            <div className="tip-row"><i style={{ background: "var(--series-det)" }} /><b>{money(h.base_net)}</b><span>Contractual net gap</span></div>
            <div className="tip-row"><i style={{ background: "var(--primary)" }} /><b>{money(h.scen_net)}</b><span>Scenario net gap</span></div>
            <div className="tip-row net"><span className="tone" data-tone={tone(h.delta)}>{h.delta > 0 ? "▲" : h.delta < 0 ? "▼" : "●"}</span><b>{money(h.delta)}</b><span>Change</span></div>
            <div className="tip-say">{changeText(h.base_net, h.scen_net)}</div>
            <div className="tip-hint">
              Assets {formatCompact(h.base_assets)} → {formatCompact(h.scen_assets)} · liabilities {formatCompact(h.base_liabilities)} → {formatCompact(h.scen_liabilities)}
              <br />{formatInt(h.moved_in)} contract{h.moved_in === 1 ? "" : "s"} moved in · {formatInt(h.moved_out)} moved out
            </div>
          </div>
        )}
      </div>
    </figure>
  );
}
