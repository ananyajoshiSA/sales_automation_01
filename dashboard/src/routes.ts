// Route rules and the cached /api/summary flow. Kept out of index.ts because a Worker's entry
// module may only export handlers.
import { cacheDecision, fitsCache, PAUSED_UNTIL_IST, pausedBody, spanDays, sumRowsRead } from "./cache";
import { fmtUtc, parseUtc } from "./lsq";
import { summary } from "./metrics";
import { Env, Task, addReadBudget, addWriteBudget, num, utcDay } from "./tasks";

export const DAY = /^\d{4}-\d{2}-\d{2}$/;
export const TASKS: Task[] = ["calls_out", "calls_in", "leads", "zip", "enroll", "users", "arrivals"];
export const MAX_RANGE_DAYS = 31;
// Ingest stops at WRITE_BUDGET (90k); cache writes may use the rest up to just under the free 100k.
const SUMMARY_WRITE_STOP = 99_000;
export const PAUSED_MESSAGE = `Numbers paused until ${PAUSED_UNTIL_IST} IST: the free daily data limit was reached.`;

const HEADERS = { "content-type": "application/json", "cache-control": "no-store" };
export const json = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: HEADERS });

export function parseRange(params: URLSearchParams, today: string): { from: string; to: string } | { error: string } {
  const from = params.get("from") ?? today;
  const to = params.get("to") ?? today;
  if (!DAY.test(from) || !DAY.test(to) || from > to) return { error: "from/to must be YYYY-MM-DD, from <= to" };
  if (spanDays(from, to) > MAX_RANGE_DAYS) return { error: `Pick at most ${MAX_RANGE_DAYS} days` };
  return { from, to };
}

/** Constant-time check of `Authorization: Bearer <RUN_TOKEN>`; no token configured = always refused. */
export function runAuthorized(header: string | null, token: string | undefined): boolean {
  if (!token) return false;
  const want = `Bearer ${token}`, got = header ?? "";
  let diff = want.length ^ got.length;
  for (let i = 0; i < want.length; i++) diff |= want.charCodeAt(i) ^ (got.charCodeAt(i) || 0);
  return diff === 0;
}

/**
 * Cached summary. One D1 batch reads the cached body and today's read/write totals; a fresh copy is
 * returned as stored text. Otherwise the summary is computed (if the read budget allows), cached,
 * and its reads and writes are added to the budgets. Over budget, the last copy is served marked
 * paused, or 503 if there is none.
 */
export async function serveSummary(env: Env, from: string, to: string): Promise<Response> {
  const db = env.DB;
  const day = utcDay();
  const key = `${from}|${to}`;
  const pre = await db.batch([
    db.prepare("SELECT computed_at, body FROM summary_cache WHERE range_key = ?").bind(key),
    db.prepare("SELECT rows FROM read_budget WHERE day = ?").bind(day),
    db.prepare("SELECT rows FROM write_budget WHERE day = ?").bind(day),
  ]);
  const row = (pre[0].results[0] as { computed_at: string; body: string } | undefined) ?? null;
  const readsToday = (pre[1].results[0] as { rows: number } | undefined)?.rows ?? 0;
  const writesToday = (pre[2].results[0] as { rows: number } | undefined)?.rows ?? 0;
  const readBudget = num(env.READ_BUDGET, 4_500_000);
  const decision = cacheDecision({ row, nowMs: Date.now(), ttlSec: num(env.SUMMARY_TTL_SECONDS, 60), readsToday,
    readBudget, writesBlocked: writesToday >= SUMMARY_WRITE_STOP });

  if (decision === "hit") return new Response(row!.body, { headers: { ...HEADERS, "x-cache": "hit" } });
  if (decision === "paused_stale") return new Response(pausedBody(row!), { headers: { ...HEADERS, "x-cache": "stale" } });
  if (decision === "unavailable") {
    return new Response(JSON.stringify({ error: PAUSED_MESSAGE, paused: { untilIst: PAUSED_UNTIL_IST, dataAsOfIst: null } }),
      { status: 503, headers: { ...HEADERS, "x-cache": "none" } });
  }

  const { data, rowsRead } = await summary(db, from, to, num(env.WRITE_BUDGET, 90_000));
  const read = rowsRead + sumRowsRead(pre);
  const body = JSON.stringify({ ...data, budget: { ...data.budget, rowsReadToday: readsToday + read, readLimit: readBudget } });
  const writes = [addReadBudget(db, read)];
  if (fitsCache(body)) {
    writes.push(db.prepare(
      "INSERT INTO summary_cache (range_key, computed_at, rows_read, body) VALUES (?, ?, ?, ?) " +
      "ON CONFLICT(range_key) DO UPDATE SET computed_at = excluded.computed_at, rows_read = excluded.rows_read, body = excluded.body",
    ).bind(key, fmtUtc(new Date()), read, body));
  }
  const res = await db.batch(writes);
  await addWriteBudget(db, res.reduce((n, r) => n + (r.meta?.rows_written ?? 0), 0)).run();
  return new Response(body, { headers: { ...HEADERS, "x-cache": "miss" } });
}

export async function health(env: Env): Promise<Response> {
  const day = utcDay();
  const [sync, w, r] = await env.DB.batch([
    env.DB.prepare("SELECT task, cursor, updated_at, last_error FROM sync_state"),
    env.DB.prepare("SELECT rows FROM write_budget WHERE day = ?").bind(day),
    env.DB.prepare("SELECT rows FROM read_budget WHERE day = ?").bind(day),
  ]);
  const now = Date.now();
  // No error text here: this route needs no login, and error messages can quote LeadSquared data.
  return json({
    ok: true,
    rowsWrittenToday: (w.results[0] as { rows: number } | undefined)?.rows ?? 0, writeBudget: num(env.WRITE_BUDGET, 90_000),
    rowsReadToday: (r.results[0] as { rows: number } | undefined)?.rows ?? 0, readBudget: num(env.READ_BUDGET, 4_500_000),
    accessConfigured: !!(env.ACCESS_TEAM_DOMAIN?.trim() && env.ACCESS_AUD?.trim()),
    sync: (sync.results as { task: string; cursor: string; updated_at: string; last_error: string | null }[]).map((s) => ({
      task: s.task, cursor: s.cursor, updatedAt: s.updated_at, hasError: !!s.last_error,
      // Only the status code of a failed LeadSquared call (e.g. 401 = keys missing or wrong), never its text.
      errorHttp: Number(s.last_error?.match(/-> HTTP (\d{3})/)?.[1]) || null,
      behindMin: ["users", "enroll"].includes(s.task) ? null : Math.round((now - (parseUtc(s.cursor)?.getTime() ?? 0)) / 60_000),
    })),
  });
}

/** `wrangler dev` only: a localhost request with ACCESS_LOCAL_DEV=1 (set in .dev.vars) skips the
 *  Access check. A deployed Worker never sees a localhost hostname. */
export function localDev(url: URL, env: Env): boolean {
  return env.ACCESS_LOCAL_DEV === "1" && (url.hostname === "localhost" || url.hostname === "127.0.0.1");
}

