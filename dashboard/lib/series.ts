import type { Bucket, SeriesPoint } from "./queries.ts";

const STEP_MS: Record<Bucket, number> = { minute: 60_000, hour: 3_600_000, day: 86_400_000 };

export type Bar = { at: number; requests: number; fallbacks: number; hot: boolean };

/** Start of the bucket that contains `t` (UTC, matching the database's date_trunc). */
export const floorTo = (t: number, bucket: Bucket) => Math.floor(t / STEP_MS[bucket]) * STEP_MS[bucket];

/**
 * One bar per bucket across the whole window, zero-filled where nothing happened, so a quiet hour
 * shows as a gap instead of the chart squeezing its neighbours together. A bar is "hot" when more than
 * 5% of its requests needed a fallback.
 */
export function fillSeries(series: SeriesPoint[], hours: number, bucket: Bucket, now = Date.now()): Bar[] {
  const step = STEP_MS[bucket];
  const end = floorTo(now, bucket);
  const start = floorTo(now - hours * 3_600_000, bucket);
  const byAt = new Map(series.map((p) => [floorTo(new Date(p.at).getTime(), bucket), p]));
  const bars: Bar[] = [];
  for (let t = start; t <= end; t += step) {
    const p = byAt.get(t);
    const requests = p?.requests ?? 0;
    const fallbacks = p?.fallbacks ?? 0;
    bars.push({ at: t, requests, fallbacks, hot: requests >= 5 && fallbacks / requests > 0.05 });
  }
  return bars;
}

/** A round number at or above `max`, for the top gridline: 1, 2, 5 times a power of ten. */
export function niceMax(max: number): number {
  if (max <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(max));
  for (const m of [1, 2, 5, 10]) if (m * p >= max) return m * p;
  return 10 * p;
}
