export const formatInt = (n: number): string => n.toLocaleString("en-US");

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export const formatDuration = (ms: number): string => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

export const formatMoney = (n: number): string =>
  n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** Compact figure for chart labels: 140.6M, -591.8M, 12.3K. */
export function formatCompact(n: number): string {
  const a = Math.abs(n);
  const s = a >= 1e9 ? `${(a / 1e9).toFixed(2)}B` : a >= 1e6 ? `${(a / 1e6).toFixed(1)}M` : a >= 1e3 ? `${(a / 1e3).toFixed(1)}K` : a.toFixed(0);
  return n < 0 ? `-${s}` : s;
}

export const formatPct = (p: number, digits = 0): string => `${(p * 100).toFixed(digits)}%`;

/** Signed compact money for deltas: +12.3M / −4.5M (true minus sign). */
export function formatSignedCompact(n: number): string {
  if (n === 0) return "0";
  const s = formatCompact(Math.abs(n));
  return n > 0 ? `+${s}` : `−${s}`;
}
