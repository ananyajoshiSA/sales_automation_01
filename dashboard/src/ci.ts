// Conversation Intelligence & Team Analytics (public/ci.html). The analysis runs offline in Python
// (analytics/convintel) and analytics/convintel/d1push.py loads one snapshot per date range into D1, split
// into parts under 90 KB. The Worker only serves them and never writes the ci tables:
//   GET /api/ci/ranges               the snapshots loaded, for the range buttons
//   GET /api/ci/snapshot?range=KEY   one snapshot (KEY: today | yesterday | 7d | 30d | YYYY-MM-DD)
// One D1 query per request. The parts are joined as text and returned without JSON.parse: a 30-day
// snapshot runs to a megabyte or more, and parsing it would use up the 10 ms CPU budget.

export interface CiEnv { DB: D1Database; CI_ENABLED?: string }

export const CI_RANGE = /^(today|yesterday|7d|30d|\d{4}-\d{2}-\d{2})$/;
export const UPDATING = "This view is being updated. Try again in a minute.";
export const CI_OFF = "Conversation intelligence is not switched on for this dashboard.";
export const NO_SNAPSHOT = "No conversation intelligence data for this range yet.";
// Snapshots change only when the offline job loads a new one, so a minute in the browser is safe.
const CACHE = "private, max-age=60";

export interface CiPart { part: number; parts: number; generated_at: string; body: string }
interface IndexRow { range_key: string; label: string; day_from: string; day_to: string; generated_at: string; parts: number; bytes: number }

const reply = (body: string, status: number, extra: Record<string, string> = {}) =>
  new Response(body, { status, headers: { "content-type": "application/json", "cache-control": status === 200 ? CACHE : "no-store", ...extra } });
const fail = (error: string, status: number, extra: Record<string, string> = {}) => reply(JSON.stringify({ error }), status, extra);

/** The snapshot text when the rows (ordered by part) are one complete load, else null: a load is under way
 *  or stopped part-way, and mixing parts of two loads would hand the page broken JSON. */
export function assemble(rows: CiPart[]): string | null {
  if (!rows.length) return null;
  const { parts, generated_at } = rows[0];
  if (rows.length !== parts) return null;
  for (let i = 0; i < rows.length; i++) {
    const r = rows[i];
    if (r.part !== i + 1 || r.parts !== parts || r.generated_at !== generated_at || typeof r.body !== "string") return null;
  }
  return rows.map((r) => r.body).join("");
}

/** Rows of a ci table; none while migration 0004 is not applied yet (e.g. a local D1 not migrated), so the page
 *  shows its "nothing loaded yet" state instead of a database error. */
async function rowsOf<T>(stmt: D1PreparedStatement): Promise<T[]> {
  try {
    return (await stmt.all<T>()).results;
  } catch (err) {
    if (/no such table: ci_snapshot/i.test(err instanceof Error ? err.message : String(err))) return [];
    throw err;
  }
}

/** /api/ci/* after the Access check (index.ts). */
export async function serveCi(url: URL, req: Request, env: CiEnv): Promise<Response> {
  if (env.CI_ENABLED !== "1") return fail(CI_OFF, 404);
  if (req.method !== "GET" && req.method !== "HEAD") return fail("GET only", 405);
  if (url.pathname === "/api/ci/ranges") {
    const results = await rowsOf<IndexRow>(env.DB.prepare(
      "SELECT range_key, label, day_from, day_to, generated_at, parts, bytes FROM ci_snapshot_index ORDER BY " +
      "CASE range_key WHEN 'today' THEN 0 WHEN 'yesterday' THEN 1 WHEN '7d' THEN 2 WHEN '30d' THEN 3 ELSE 4 END, range_key DESC",
    ));
    return reply(JSON.stringify({ ranges: results.map((r) => ({ key: r.range_key, label: r.label, from: r.day_from,
      to: r.day_to, generatedAt: r.generated_at, parts: r.parts, bytes: r.bytes })) }), 200);
  }
  if (url.pathname === "/api/ci/snapshot") {
    const key = url.searchParams.get("range") ?? "";
    if (!CI_RANGE.test(key)) return fail("range must be today, yesterday, 7d, 30d or a day as YYYY-MM-DD", 400);
    const results = await rowsOf<CiPart>(env.DB.prepare(
      "SELECT part, parts, generated_at, body FROM ci_snapshot WHERE range_key = ? ORDER BY part",
    ).bind(key));
    if (!results.length) return fail(NO_SNAPSHOT, 404);
    const body = assemble(results);
    if (body === null) return fail(UPDATING, 503, { "retry-after": "60" });
    return reply(body, 200, { "x-ci-generated-at": results[0].generated_at });
  }
  return fail("not found", 404);
}
