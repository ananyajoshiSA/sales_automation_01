import { describe, expect, it } from "vitest";
import { MAX_CACHE_BYTES, cacheDecision, dataAsOfIst, fitsCache, pausedBody, spanDays, sumRowsRead } from "../src/cache";

const now = Date.parse("2026-10-09T07:41:00Z");
const row = (ageSec: number) => ({ computed_at: new Date(now - ageSec * 1000).toISOString().slice(0, 19).replace("T", " "), body: "{}" });
const base = { nowMs: now, ttlSec: 60, readsToday: 1000, readBudget: 4_500_000 };

describe("cacheDecision", () => {
  it("serves a fresh copy and recomputes a stale one while the budget allows", () => {
    expect(cacheDecision({ ...base, row: row(10) })).toBe("hit");
    expect(cacheDecision({ ...base, row: row(120) })).toBe("compute");
    expect(cacheDecision({ ...base, row: null })).toBe("compute");
  });

  it("treats the TTL as exclusive", () => {
    expect(cacheDecision({ ...base, row: row(59) })).toBe("hit");
    expect(cacheDecision({ ...base, row: row(60) })).toBe("compute");
  });

  it("pauses on the stale copy once the read budget is spent, or has nothing to show", () => {
    const spent = { ...base, readsToday: 4_500_000 };
    expect(cacheDecision({ ...spent, row: row(30) })).toBe("hit");
    expect(cacheDecision({ ...spent, row: row(3600) })).toBe("paused_stale");
    expect(cacheDecision({ ...spent, row: null })).toBe("unavailable");
    expect(cacheDecision({ ...base, row: row(3600), writesBlocked: true })).toBe("paused_stale");
  });

  it("does not trust a copy stamped in the future or with a bad time", () => {
    expect(cacheDecision({ ...base, row: row(-30) })).toBe("compute");
    expect(cacheDecision({ ...base, row: { computed_at: "garbage", body: "{}" } })).toBe("compute");
  });
});

it("sums rows read, counting missing meta as 0", () => {
  expect(sumRowsRead([{ meta: { rows_read: 5 } }, {}, null, { meta: {} }, { meta: { rows_read: 7 } }])).toBe(12);
  expect(sumRowsRead([])).toBe(0);
});

it("guards the cached body size", () => {
  expect(fitsCache('{"a":1}')).toBe(true);
  expect(fitsCache("x".repeat(MAX_CACHE_BYTES))).toBe(true);
  expect(fitsCache("x".repeat(MAX_CACHE_BYTES + 1))).toBe(false);
  expect(fitsCache("₹".repeat(MAX_CACHE_BYTES / 2))).toBe(false);   // 3 bytes each in UTF-8
});

it("formats the data time in IST", () => {
  expect(dataAsOfIst("2026-10-09 07:40:00")).toBe("13:10");
  expect(dataAsOfIst("2026-10-09 20:00:00")).toBe("01:30");
  expect(dataAsOfIst("")).toBe("");
});

it("marks a stale body as paused without parsing it", () => {
  const out = JSON.parse(pausedBody({ computed_at: "2026-10-09 07:40:00", body: '{"range":{"from":"a","to":"b"}}' }));
  expect(out).toEqual({ paused: { untilIst: "05:30", dataAsOfIst: "13:10" }, range: { from: "a", to: "b" } });
});

it("counts range days inclusively", () => {
  expect(spanDays("2026-10-09", "2026-10-09")).toBe(1);
  expect(spanDays("2026-09-09", "2026-10-09")).toBe(31);
  expect(spanDays("2026-09-08", "2026-10-09")).toBe(32);
});
