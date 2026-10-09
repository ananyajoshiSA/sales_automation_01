// Summary cache and D1 read budget (free tier: 5,000,000 rows read per UTC day). Decisions are pure
// so they can be tested without D1. A cache hit returns the stored JSON text as-is: no parse, no
// re-stringify, so a hit costs one small D1 batch and almost no CPU.
import { parseUtc } from "./lsq";

export type CacheDecision = "hit" | "compute" | "paused_stale" | "unavailable";

export interface CacheRow { computed_at: string; body: string }

const IST_MS = 330 * 60 * 1000;
// D1 rows and strings are capped at 2 MB; a summary is ~100 KB, so anything near the cap is a bug.
export const MAX_CACHE_BYTES = 1_000_000;

export function cacheDecision({ row, nowMs, ttlSec, readsToday, readBudget, writesBlocked = false }: {
  row: CacheRow | null; nowMs: number; ttlSec: number; readsToday: number; readBudget: number; writesBlocked?: boolean;
}): CacheDecision {
  const at = row ? parseUtc(row.computed_at)?.getTime() : undefined;
  if (row && at !== undefined && nowMs - at >= 0 && nowMs - at < ttlSec * 1000) return "hit";
  // Without a write the reads can't be counted, so a write stop pauses summaries too.
  if (readsToday < readBudget && !writesBlocked) return "compute";
  return row ? "paused_stale" : "unavailable";
}

/** Sum of meta.rows_read over D1 results; a result without meta counts 0. */
export function sumRowsRead(results: ({ meta?: { rows_read?: number } } | null | undefined)[]): number {
  return results.reduce((n, r) => n + (Number(r?.meta?.rows_read) || 0), 0);
}

export function fitsCache(body: string): boolean {
  return body.length * 3 <= MAX_CACHE_BYTES || new TextEncoder().encode(body).length <= MAX_CACHE_BYTES;
}

/** 'HH:MM' IST for a Date. */
export function istClock(d: Date): string {
  return new Date(d.getTime() + IST_MS).toISOString().slice(11, 16);
}

/** 'HH:MM' IST for a stored UTC 'YYYY-MM-DD HH:MM:SS'. */
export function dataAsOfIst(utc: string): string {
  const d = parseUtc(utc);
  return d ? istClock(d) : "";
}

export const PAUSED_UNTIL_IST = "05:30";   // D1's daily limits reset at 00:00 UTC

/** The stale cached body with a `paused` object spliced in front, again without parsing it. */
export function pausedBody(row: CacheRow): string {
  const paused = JSON.stringify({ untilIst: PAUSED_UNTIL_IST, dataAsOfIst: dataAsOfIst(row.computed_at) });
  return row.body.startsWith("{") && row.body.length > 2 ? `{"paused":${paused},${row.body.slice(1)}` : row.body;
}

/** Inclusive number of days from one 'YYYY-MM-DD' to another. */
export function spanDays(from: string, to: string): number {
  return Math.round((Date.parse(to + "T00:00:00Z") - Date.parse(from + "T00:00:00Z")) / 86_400_000) + 1;
}
