import { int } from "@/lib/format";
import type { Bucket, SeriesPoint } from "@/lib/queries";
import { fillSeries, niceMax } from "@/lib/series";

const W = 650;
const H = 200;
const LEFT = 46;
const RIGHT = 8;
const TOP = 30;
const BASE = 160;

const two = (n: number) => String(n).padStart(2, "0");
function label(t: number, bucket: Bucket): string {
  const d = new Date(t);
  if (bucket === "day") return `${d.toLocaleString("en-US", { month: "short", timeZone: "UTC" })} ${d.getUTCDate()}`;
  return `${two(d.getUTCHours())}:${two(d.getUTCMinutes())}`;
}

/** Requests per bucket as bars. Orange bars had more than 5% fallbacks; the busiest bar is bright. Times are UTC. */
export function RequestsChart({ series, hours, bucket }: { series: SeriesPoint[]; hours: number; bucket: Bucket }) {
  const bars = fillSeries(series, hours, bucket);
  const max = Math.max(0, ...bars.map((b) => b.requests));
  const top = niceMax(max);
  const slot = (W - LEFT - RIGHT) / bars.length;
  const bw = Math.max(1.5, Math.min(24, slot * 0.7));
  const peak = max > 0 ? bars.findIndex((b) => b.requests === max) : -1;
  const y = (v: number) => BASE - (v / top) * (BASE - TOP);
  const ticks = [0, 0.5, 1].map((f) => ({ v: Math.round(top * f), y: y(top * f) }));
  const labelAt = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round(f * (bars.length - 1)));

  return (
    <>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" className="chart" role="img" aria-label={`Requests per ${bucket}, highest ${int(max)}`}>
        <g className="grid">
          {ticks.map((t) => (
            <line key={t.y} x1={LEFT} x2={W - RIGHT} y1={t.y} y2={t.y} className={t.v === 0 ? "base" : undefined} />
          ))}
        </g>
        {ticks.map((t) => (
          <text key={`l${t.y}`} x={LEFT - 8} y={t.y + 4} textAnchor="end">
            {int(t.v)}
          </text>
        ))}
        {bars.map((b, i) => {
          const h = Math.max(b.requests > 0 ? 1.5 : 0, BASE - y(b.requests));
          return (
            <rect key={b.at} x={LEFT + i * slot + (slot - bw) / 2} y={BASE - h} width={bw} height={h} rx={Math.min(2, bw / 2)} className={i === peak ? "b-peak" : b.hot ? "b-fb" : "b-req"}>
              <title>{`${label(b.at, bucket)} UTC: ${int(b.requests)} requests, ${int(b.fallbacks)} with fallbacks`}</title>
            </rect>
          );
        })}
        {labelAt.map((i, n) => (
          <text key={`x${i}-${n}`} x={LEFT + i * slot + slot / 2} y={H - 14} textAnchor={n === 0 ? "start" : n === labelAt.length - 1 ? "end" : "middle"}>
            {label(bars[i].at, bucket)}
          </text>
        ))}
      </svg>
      <div className="legend">
        <span>
          <i style={{ background: "var(--sig)" }} />
          Requests
        </span>
        {peak >= 0 ? (
          <span>
            <i style={{ background: "var(--bright)" }} />
            Busiest: {int(max)} at {label(bars[peak].at, bucket)} UTC
          </span>
        ) : null}
        <span>
          <i style={{ background: "var(--patch)" }} />
          Fallbacks above 5%
        </span>
      </div>
    </>
  );
}
