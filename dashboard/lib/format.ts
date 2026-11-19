export const ms = (v: number | null | undefined) =>
  v === null || v === undefined ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v)} ms`;

/** Two durations that share a unit: "81 / 253 ms", or "1.18 / 2.34 s" once either is a second or more. */
export function msPair(a: number | null | undefined, b: number | null | undefined): string {
  if (a === null || a === undefined || b === null || b === undefined) return `${ms(a)} / ${ms(b)}`;
  return Math.max(a, b) >= 1000 ? `${(a / 1000).toFixed(2)} / ${(b / 1000).toFixed(2)} s` : `${Math.round(a)} / ${Math.round(b)} ms`;
}

export const pct = (v: number) => `${(v * 100).toFixed(1)}%`;

export const usd = (v: number) =>
  v === 0 ? "$0" : v < 0.01 ? `$${v.toFixed(6)}` : `$${v.toFixed(4)}`;

export const int = (v: number) => v.toLocaleString("en-US");

export const ago = (iso: string) => {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
};
