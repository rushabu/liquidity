import { useEffect, useRef, useState } from "react";
import type { MonthPoint } from "../../api/client";
import { formatCompact, formatSignedCompact } from "../../lib/format";

/** Cumulative net gap month by month (deterministic vs AI-adjusted, divergence shaded) and, underneath on its
 *  own axis, the monthly difference AI − deterministic as bars you can click. Pure SVG at real pixel width. */

const TOP_H = 250;
const GAP_H = 34;
const BAR_H = 104;
const H = TOP_H + GAP_H + BAR_H + 18;
const M = { left: 62, right: 14, top: 14 };

const axis = (n: number) => {
  const a = Math.abs(n);
  const [d, u] = a >= 1e9 ? [1e9, "B"] : a >= 1e6 ? [1e6, "M"] : a >= 1e3 ? [1e3, "K"] : [1, ""];
  return `${n < 0 ? "−" : n > 0 ? "+" : ""}${+(a / d).toFixed(1)}${u}`;
};

function ticks(lo: number, hi: number, n = 4): number[] {
  const span = Math.max(1, hi - lo);
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? 10 * mag;
  const out: number[] = [];
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : v);
  if (out[out.length - 1] < hi) out.push(out[out.length - 1] + step);
  return out;
}

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

export function MonthlyChart({ months, currency, selected, onSelect }: {
  months: MonthPoint[];
  currency: string;
  selected: number | null;
  onSelect: (month: number) => void;
}) {
  const [ref, W] = useWidth(720);
  const [hover, setHover] = useState<number | null>(null);
  const n = months.length - 1;
  const plotW = W - M.left - M.right;
  const x = (m: number) => M.left + (m / Math.max(1, n)) * plotW;

  const cums = months.flatMap((m) => [m.det_cum, m.ai_cum, 0]);
  const t1 = ticks(Math.min(...cums), Math.max(...cums));
  const [lo1, hi1] = [t1[0], t1[t1.length - 1]];
  const y1 = (v: number) => M.top + ((hi1 - v) / (hi1 - lo1 || 1)) * (TOP_H - M.top - 22);

  const maxDiff = Math.max(1, ...months.map((m) => Math.abs(m.diff)));
  const barTop = TOP_H + GAP_H;
  const zeroBar = barTop + BAR_H / 2;
  const y2 = (v: number) => zeroBar - (v / maxDiff) * (BAR_H / 2 - 4);
  const bw = Math.max(3, Math.min(18, (plotW / Math.max(1, n)) * 0.62));

  const path = (key: "det_cum" | "ai_cum") => months.map((m, i) => `${i ? "L" : "M"}${x(m.month)},${y1(m[key])}`).join("");
  const band = `${months.map((m, i) => `${i ? "L" : "M"}${x(m.month)},${y1(m.ai_cum)}`).join("")}`
    + [...months].reverse().map((m) => `L${x(m.month)},${y1(m.det_cum)}`).join("") + "Z";
  const step = n > 12 ? 3 : 1;
  const h = hover === null ? null : months[hover];
  const tipLeft = h ? Math.min(Math.max(0, x(h.month) + 10), W - 230) : 0;
  const summary = `Cumulative net gap over ${n} months in ${currency}: deterministic ends at ${axis(months[n]?.det_cum ?? 0)}, `
    + `AI-adjusted at ${axis(months[n]?.ai_cum ?? 0)}.`;

  return (
    <figure className="monthly">
      <figcaption>
        <ul className="gapchart-legend monthly-legend">
          <li><span className="line-key" style={{ background: "var(--series-det)" }} />Deterministic</li>
          <li><span className="line-key" style={{ background: "var(--series-ai)" }} />AI-adjusted</li>
          <li><span className="key" style={{ background: "var(--ai-soft)" }} />Divergence</li>
        </ul>
      </figcaption>
      <div className="gapchart-plot" ref={ref} onPointerLeave={() => setHover(null)}>
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={summary}>
          {t1.map((t) => (
            <g key={`t${t}`}>
              <line x1={M.left} x2={W - M.right} y1={y1(t)} y2={y1(t)} stroke="var(--grid)" strokeDasharray={t === 0 ? "4 4" : undefined}
                    style={t === 0 ? { stroke: "var(--axis)" } : undefined} />
              <text x={M.left - 8} y={y1(t)} dy="0.32em" textAnchor="end" className="gapchart-tick">{axis(t)}</text>
            </g>
          ))}
          {months.filter((m) => m.month % step === 0).map((m) => (
            <text key={`x${m.month}`} x={x(m.month)} y={TOP_H - 4} textAnchor="middle" className="gapchart-tick">M{m.month}</text>
          ))}
          <path d={band} fill="var(--ai-soft)" stroke="none" />
          <path d={path("det_cum")} fill="none" stroke="var(--series-det)" strokeWidth={2} />
          <path d={path("ai_cum")} fill="none" stroke="var(--series-ai)" strokeWidth={2} />
          {h && (
            <g>
              <line x1={x(h.month)} x2={x(h.month)} y1={M.top} y2={TOP_H - 20} stroke="var(--axis)" />
              <circle cx={x(h.month)} cy={y1(h.det_cum)} r={4} fill="var(--surface)" stroke="var(--series-det)" strokeWidth={2} />
              <circle cx={x(h.month)} cy={y1(h.ai_cum)} r={4} fill="var(--surface)" stroke="var(--series-ai)" strokeWidth={2} />
            </g>
          )}

          <text x={M.left} y={barTop - 10} className="monthly-sub">AI minus deterministic, by month ({currency}) · click a bar to drill down</text>
          <line x1={M.left} x2={W - M.right} y1={zeroBar} y2={zeroBar} stroke="var(--axis)" />
          <text x={M.left - 8} y={barTop + 6} dy="0.32em" textAnchor="end" className="gapchart-tick">{axis(maxDiff)}</text>
          <text x={M.left - 8} y={barTop + BAR_H - 6} dy="0.32em" textAnchor="end" className="gapchart-tick">{axis(-maxDiff)}</text>
          {months.slice(1).map((m) => {
            const cx = x(m.month);
            const top = Math.min(zeroBar, y2(m.diff));
            const hgt = Math.max(m.diff === 0 ? 0 : 1, Math.abs(y2(m.diff) - zeroBar));
            return (
              <g key={`b${m.month}`}>
                {selected === m.month && <rect x={cx - bw / 2 - 4} y={barTop} width={bw + 8} height={BAR_H} rx={4} className="month-selected" />}
                {hgt > 0 && <rect x={cx - bw / 2} y={top} width={bw} height={hgt} rx={2} fill={m.diff > 0 ? "var(--success)" : "var(--error)"} />}
              </g>
            );
          })}
          {months.map((m, i) => (
            <rect key={`h${m.month}`} className="gapchart-hit" data-clickable={m.month ? "" : undefined}
                  x={x(m.month) - plotW / Math.max(1, n) / 2} y={0} width={plotW / Math.max(1, n)} height={H}
                  onPointerEnter={() => setHover(i)} onClick={() => m.month && onSelect(m.month)}>
              <title>Month {m.month}</title>
            </rect>
          ))}
        </svg>
        {h && (
          <div className="gapchart-tip" style={{ left: tipLeft, top: M.top, width: 220 }}>
            <div className="tip-title"><b>Month {h.month}</b><span>to {h.end_date}{h.bucket ? ` · ${h.bucket}` : ""}</span></div>
            <div className="tip-row"><i style={{ background: "var(--series-det)" }} /><b>{formatCompact(h.det_cum)}</b><span>Deterministic</span></div>
            <div className="tip-row"><i style={{ background: "var(--series-ai)" }} /><b>{formatCompact(h.ai_cum)}</b><span>AI-adjusted</span></div>
            {h.month > 0 && <div className="tip-row net"><span /><b>{formatSignedCompact(h.diff)}</b><span>this month</span></div>}
            {h.month > 0 && <div className="tip-hint">{h.note}</div>}
          </div>
        )}
      </div>
    </figure>
  );
}
