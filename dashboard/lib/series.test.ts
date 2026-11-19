import assert from "node:assert/strict";
import { test } from "node:test";
import { fillSeries, floorTo, niceMax } from "./series.ts";

const NOW = Date.UTC(2026, 10, 18, 16, 30);

test("every hour in the window gets a bar, zero-filled", () => {
  const bars = fillSeries([{ at: "2026-11-18T15:00:00Z", requests: 40, fallbacks: 0 }], 3, "hour", NOW);
  // 13:00 .. 16:00 inclusive, because the window starts mid-hour and the current hour is still open
  assert.equal(bars.length, 4);
  assert.deepEqual(bars.map((b) => b.requests), [0, 0, 40, 0]);
});

test("a bar is hot only when fallbacks are above 5% of a meaningful sample", () => {
  const bars = fillSeries(
    [
      { at: "2026-11-18T14:00:00Z", requests: 100, fallbacks: 6 },
      { at: "2026-11-18T15:00:00Z", requests: 100, fallbacks: 5 },
      { at: "2026-11-18T16:00:00Z", requests: 2, fallbacks: 2 },
    ],
    3,
    "hour",
    NOW,
  );
  assert.deepEqual(bars.map((b) => b.hot), [false, true, false, false]);
});

test("buckets line up with the database's UTC truncation", () => {
  assert.equal(floorTo(Date.UTC(2026, 10, 18, 16, 59, 59), "hour"), Date.UTC(2026, 10, 18, 16));
  assert.equal(floorTo(Date.UTC(2026, 10, 18, 16, 59, 59), "day"), Date.UTC(2026, 10, 18));
  assert.equal(floorTo(Date.UTC(2026, 10, 18, 16, 30, 40), "minute"), Date.UTC(2026, 10, 18, 16, 30));
});

test("the top gridline is a round number at or above the data", () => {
  assert.equal(niceMax(0), 1);
  assert.equal(niceMax(1320), 2000);
  assert.equal(niceMax(87), 100);
  assert.equal(niceMax(5), 5);
  assert.equal(niceMax(2300), 5000);
});
