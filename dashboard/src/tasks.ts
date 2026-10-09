// Cron ingest tasks. Each run pulls a small slice from LeadSquared and writes its totals and its
// new cursor in ONE D1 batch (a transaction), so a run cut off by the 10 ms CPU limit or an API
// error writes nothing and the next run retries the same slice.
import {
  Activity, Call, ENROLLED, LEAD_ASSIGNED, LsqEnv, PHONE_INBOUND, PHONE_OUTBOUND, ZIP_NOTES, activitiesByEvent, fmtUtc,
  getUsers, istDay, leadActivities, leadsGet, parseCall, parseUtc, stageChanges,
} from "./lsq";
import { SHARED_TEAM, resolveArrival, sharedNames } from "./accountability";
import { aggregateCalls, aggregateLeads, aggregateZip, firstEnrollment } from "./aggregate";
import { teamFromGroups } from "./metrics";
import { addOnConflict, int, multiInsert, str } from "./sql";
import { sumRowsRead } from "./cache";

export interface Env extends LsqEnv {
  DB: D1Database;
  CALL_RECORDS_PER_RUN?: string;   // default 200 calls (≈1.6 KB of JSON each) per run
  CALL_PAGE_SIZE?: string;         // default 100
  ZIP_RECORDS_PER_RUN?: string;    // default 80 notes (≈3.7 KB each) per run
  ZIP_PAGE_SIZE?: string;          // default 40
  LEAD_PAGE_SIZE?: string;         // default 500 (4 small columns)
  ENROLL_LEADS_PER_RUN?: string;   // default 12 (one stage-history request each)
  ARRIVAL_LEADS_PER_RUN?: string;  // default 8 (one owner-change-history request each)
  SHARED_ACCOUNTS?: string;        // logins several people use, comma-separated; default "Rinku Jhala,Admin"
  WRITE_BUDGET?: string;           // default 90000 of the 100,000 free rows written per UTC day
  READ_BUDGET?: string;            // default 4500000 of the 5,000,000 free rows read per UTC day
  SUMMARY_TTL_SECONDS?: string;    // default 60: how long a computed summary is served from cache
  ACCESS_TEAM_DOMAIN?: string;     // <team>.cloudflareaccess.com; empty = API refuses (see access.ts)
  ACCESS_AUD?: string;             // the Access application's AUD tag
  RUN_TOKEN?: string;              // secret: Bearer token for POST /api/run
  ACCESS_LOCAL_DEV?: string;       // "1" in .dev.vars only: localhost requests skip the Access check
}

export type Task = "calls_out" | "calls_in" | "leads" | "zip" | "enroll" | "users" | "arrivals";

const MIN = 60_000;
const WINDOW_MIN = 5;          // activity windows
const LAG_MIN = 10;            // only read windows that closed at least this long ago
const FIRST_RUN_LOOKBACK_MIN = 60;
const ENROLL_MAX_AGE_DAYS = 7; // older first enrollments come from the backfill script
const USERS_EVERY_HOURS = 20;

export const num = (v: string | undefined, d: number) => (v && Number.isFinite(Number(v)) ? Number(v) : d);

interface State { cursor: Date; page: number }

async function getState(db: D1Database, task: string): Promise<State | null> {
  const r = await db.prepare("SELECT cursor, page FROM sync_state WHERE task = ?").bind(task)
    .first<{ cursor: string; page: number }>();
  const cursor = r ? parseUtc(r.cursor) : null;
  return r && cursor ? { cursor, page: r.page } : null;
}

const defaultState = (): State => ({ cursor: new Date(Date.now() - FIRST_RUN_LOOKBACK_MIN * MIN), page: 1 });

function stateSql(task: string, s: State): string {
  return `INSERT INTO sync_state (task, cursor, page, updated_at, last_error) VALUES (${str(task)}, ${str(fmtUtc(s.cursor))}, ${int(s.page)}, ${str(fmtUtc(new Date()))}, NULL) ` +
    "ON CONFLICT(task) DO UPDATE SET cursor = excluded.cursor, page = excluded.page, updated_at = excluded.updated_at, last_error = NULL";
}

async function writeBatch(db: D1Database, statements: string[]): Promise<number> {
  if (!statements.length) return 0;
  const res = await db.batch(statements.map((s) => db.prepare(s)));
  return res.reduce((n, r) => n + (r.meta?.rows_written ?? 0), 0);
}

export const utcDay = () => new Date().toISOString().slice(0, 10);

export async function rowsWrittenToday(db: D1Database): Promise<number> {
  const r = await db.prepare("SELECT rows FROM write_budget WHERE day = ?").bind(utcDay()).first<{ rows: number }>();
  return r?.rows ?? 0;
}

export async function rowsReadToday(db: D1Database): Promise<number> {
  const r = await db.prepare("SELECT rows FROM read_budget WHERE day = ?").bind(utcDay()).first<{ rows: number }>();
  return r?.rows ?? 0;
}

const BUDGET_UPSERT = "INSERT INTO %t (day, rows) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET rows = rows + excluded.rows";

/** Write-budget increment for `rows` written plus this upsert itself (1 row). */
export function addWriteBudget(db: D1Database, rows: number): D1PreparedStatement {
  return db.prepare(BUDGET_UPSERT.replace("%t", "write_budget")).bind(utcDay(), rows + 1);
}

export function addReadBudget(db: D1Database, rows: number): D1PreparedStatement {
  return db.prepare(BUDGET_UPSERT.replace("%t", "read_budget")).bind(utcDay(), rows);
}

/** Both budget increments in one batch; the read-budget upsert is the one extra row written. */
async function addToBudgets(db: D1Database, written: number, read: number): Promise<void> {
  await db.batch([addWriteBudget(db, written + 1), addReadBudget(db, read + 2)]);
}

/**
 * A D1 handle that adds every statement's meta.rows_read to `tally`, so a cron run can charge its
 * reads to read_budget. first() runs as all(), because first() returns no meta; every first() here
 * is a primary-key lookup, so it reads the same single row either way.
 */
export function countReads(db: D1Database, tally: { rowsRead: number }): D1Database {
  const inner = new WeakMap<object, D1PreparedStatement>();
  const add = <R extends object>(r: R): R => { tally.rowsRead += sumRowsRead((Array.isArray(r) ? r : [r]) as D1Result[]); return r; };
  const wrap = (s: D1PreparedStatement): D1PreparedStatement => {
    const w = {
      bind: (...v: unknown[]) => wrap(s.bind(...v)),
      all: () => s.all().then(add),
      run: () => s.run().then(add),
      raw: (o?: any) => s.raw(o),
      first: (col?: string) => s.all<Record<string, unknown>>().then(add).then((r) => {
        const row = r.results[0] ?? null;
        return col ? (row?.[col] ?? null) : row;
      }),
    };
    inner.set(w, s);
    return w as unknown as D1PreparedStatement;
  };
  return {
    prepare: (sql: string) => wrap(db.prepare(sql)),
    batch: (stmts: D1PreparedStatement[]) => db.batch(stmts.map((x) => inner.get(x) ?? x)).then(add),
    exec: (sql: string) => db.exec(sql),
  } as unknown as D1Database;
}

export async function recordError(db: D1Database, task: string, err: unknown): Promise<void> {
  const s = defaultState();
  const msg = `${fmtUtc(new Date())} ${err instanceof Error ? err.message : String(err)}`.slice(0, 500);
  await db.prepare(
    "INSERT INTO sync_state (task, cursor, page, updated_at, last_error) VALUES (?, ?, 1, ?, ?) " +
    "ON CONFLICT(task) DO UPDATE SET last_error = excluded.last_error",
  ).bind(task, fmtUtc(s.cursor), fmtUtc(new Date()), msg).run();
}

/**
 * Read activity windows [cursor, cursor + 5 min) up to `limit`, a page at a time. A page is fetched
 * only if a full page still fits the run's record budget, so CPU per run stays bounded; quiet
 * windows are cheap, so several are covered per run. Only activities whose CreatedOn falls inside
 * the window being read are kept, so a call edited later (returned again by the API) is never
 * counted twice.
 */
async function pullWindows(env: Env, task: string, event: number, maxRecords: number, pageSize: number,
                           limit: Date, maxFetches = 8): Promise<{ acts: Activity[]; state: State }> {
  let st = (await getState(env.DB, task)) ?? defaultState();
  const acts: Activity[] = [];
  let fetched = 0, records = 0;
  while (fetched < maxFetches && records + pageSize <= maxRecords) {
    const winEnd = new Date(Math.min(st.cursor.getTime() + WINDOW_MIN * MIN, limit.getTime()));
    if (winEnd <= st.cursor) break;
    const list = await activitiesByEvent(env, event, st.cursor, new Date(winEnd.getTime() - 1000), st.page, pageSize);
    fetched++;
    records += list.length;
    for (const a of list) {
      const t = parseUtc(a.CreatedOn);
      if (t && t >= st.cursor && t < winEnd) acts.push(a);
    }
    st = list.length >= pageSize ? { cursor: st.cursor, page: st.page + 1 } : { cursor: winEnd, page: 1 };
  }
  return { acts, state: st };
}

const CALLER_COUNTERS = ["dials", "answered", "not_answered", "failures", "real_calls", "talk_secs", "inbound", "inbound_missed"];

export async function runCalls(env: Env, task: "calls_out" | "calls_in"): Promise<number> {
  const event = task === "calls_out" ? PHONE_OUTBOUND : PHONE_INBOUND;
  const { acts, state } = await pullWindows(env, task, event, num(env.CALL_RECORDS_PER_RUN, 200),
    num(env.CALL_PAGE_SIZE, 100), new Date(Date.now() - LAG_MIN * MIN));
  const b = aggregateCalls(acts.map(parseCall).filter((c): c is Call => c !== null));
  const stmts = [
    ...multiInsert("caller_day", ["day", "user_id", "name", ...CALLER_COUNTERS],
      b.callers.map((c) => [str(c.day), str(c.userId), str(c.name), int(c.dials), int(c.answered), int(c.notAnswered),
        int(c.failures), int(c.realCalls), int(c.talkSecs), int(c.inbound), int(c.inboundMissed)]),
      "ON CONFLICT(day, user_id) DO UPDATE SET name = CASE WHEN excluded.name <> '' THEN excluded.name ELSE name END, " +
      CALLER_COUNTERS.map((c) => `${c} = ${c} + excluded.${c}`).join(", ")),
    ...multiInsert("calls_hour", ["day", "hour", "dials", "answered"],
      b.hours.map((h) => [str(h.day), int(h.hour), int(h.dials), int(h.answered)]),
      addOnConflict(["day", "hour"], ["dials", "answered"])),
    ...multiInsert("lead_last_call", ["lead_id", "user_id", "at"],
      b.lastCall.map((l) => [str(l.leadId), str(l.userId), str(fmtUtc(l.at))]),
      "ON CONFLICT(lead_id) DO UPDATE SET user_id = excluded.user_id, at = excluded.at WHERE excluded.at > lead_last_call.at"),
    stateSql(task, state),
  ];
  return writeBatch(env.DB, stmts);
}

export async function runZip(env: Env): Promise<number> {
  // Never read notes past the call cursors, so the call a note is about has already been stored.
  const [o, i] = await Promise.all([getState(env.DB, "calls_out"), getState(env.DB, "calls_in")]);
  const limits = [Date.now() - LAG_MIN * MIN, o?.cursor.getTime() ?? 0, i?.cursor.getTime() ?? 0];
  const { acts, state } = await pullWindows(env, "zip", ZIP_NOTES, num(env.ZIP_RECORDS_PER_RUN, 80),
    num(env.ZIP_PAGE_SIZE, 40), new Date(Math.min(...limits)), 4);
  const leadIds = [...new Set(acts.map((a) => String(a.RelatedProspectId ?? "")).filter(Boolean))];
  const callerOfLead = new Map<string, string>();
  if (leadIds.length) {
    const rows = await env.DB.prepare(
      `SELECT lead_id, user_id FROM lead_last_call WHERE lead_id IN (${leadIds.map(str).join(",")})`,
    ).all<{ lead_id: string; user_id: string }>();
    for (const r of rows.results) callerOfLead.set(r.lead_id, r.user_id);
  }
  const z = aggregateZip(acts, callerOfLead);
  const counters = ["analysed", "pitch_n", "pitch_sum", "probe_n", "probe_sum", "obj_n", "obj_sum",
                    "intent_rated", "intent_high", "intent_moderate", "intent_low"];
  return writeBatch(env.DB, [
    ...multiInsert("zip_day", ["day", "user_id", ...counters],
      z.map((d) => [str(d.day), str(d.userId), int(d.analysed), int(d.pitchN), int(d.pitchSum), int(d.probeN),
        int(d.probeSum), int(d.objN), int(d.objSum), int(d.intentRated), int(d.intentHigh), int(d.intentModerate),
        int(d.intentLow)]),
      addOnConflict(["day", "user_id"], counters)),
    stateSql("zip", state),
  ]);
}

/**
 * New leads by CreatedOn, ascending. Rows are counted only when CreatedOn < limit; the cursor moves
 * to `limit` once a page is not entirely before it, so bulk imports are worked through page by page.
 */
export async function runLeads(env: Env): Promise<number> {
  let st = (await getState(env.DB, "leads")) ?? defaultState();
  const limit = new Date(Date.now() - LAG_MIN * MIN);
  const size = num(env.LEAD_PAGE_SIZE, 500);
  const counted: Activity[] = [];
  for (let fetched = 0; fetched < 2 && st.cursor < limit; fetched++) {
    const rows = await leadsGet(env, { lookup: "CreatedOn", value: fmtUtc(st.cursor), op: ">=",
      columns: ["ProspectID", "CreatedOn", "Source", "OwnerId"], page: st.page, size, sortBy: "CreatedOn", desc: false });
    for (const r of rows) {
      const t = parseUtc(r.CreatedOn);
      if (t && t >= st.cursor && t < limit) counted.push(r);
    }
    const last = parseUtc(rows[rows.length - 1]?.CreatedOn);
    if (rows.length >= size && last && last < limit) st = { cursor: st.cursor, page: st.page + 1 };
    else { st = { cursor: limit, page: 1 }; break; }
  }
  return writeBatch(env.DB, [
    ...multiInsert("lead_day", ["day", "source", "owner_id", "n"],
      aggregateLeads(counted).map((d) => [str(d.day), str(d.source), str(d.ownerId), int(d.n)]),
      addOnConflict(["day", "source", "owner_id"], ["n"])),
    stateSql("leads", st),
  ]);
}

/**
 * Enrolled leads modified since the cursor (oldest first, a few per run): fetch each lead's stage
 * history and record its FIRST move to 'Course Enrolled'. INSERT OR IGNORE makes re-processing harmless.
 */
export async function runEnroll(env: Env): Promise<number> {
  const st = (await getState(env.DB, "enroll")) ?? defaultState();
  const rows = await leadsGet(env, { lookup: "ProspectStage", value: ENROLLED,
    columns: ["ProspectID", "ModifiedOn", "OwnerId"], page: 1, size: 200, sortBy: "ModifiedOn", desc: true });
  const fresh = rows
    .map((r) => ({ id: String(r.ProspectID), owner: String(r.OwnerId ?? ""), mod: parseUtc(r.ModifiedOn) }))
    .filter((r): r is { id: string; owner: string; mod: Date } => !!r.mod && r.mod > st.cursor)
    .sort((a, b) => a.mod.getTime() - b.mod.getTime());
  let batch = fresh.slice(0, num(env.ENROLL_LEADS_PER_RUN, 12));
  const lastMod = batch[batch.length - 1]?.mod;
  if (lastMod) batch = fresh.filter((r) => r.mod <= lastMod);   // keep same-timestamp leads together
  const minAt = Date.now() - ENROLL_MAX_AGE_DAYS * 24 * 60 * MIN;
  const values: string[][] = [];
  for (const r of batch) {
    const first = firstEnrollment(await stageChanges(env, r.id));
    if (first && first.at.getTime() >= minAt) {
      values.push([str(r.id), str(istDay(first.at)), str(fmtUtc(first.at)), str(r.owner), str(first.setBy)]);
    }
  }
  const next: State = { cursor: lastMod ?? st.cursor, page: 1 };
  return writeBatch(env.DB, [
    ...multiInsert("enrollment", ["lead_id", "day", "at", "owner_id", "set_by"], values, "ON CONFLICT(lead_id) DO NOTHING"),
    stateSql("enroll", next),
  ]);
}

/**
 * Leads whose Assigned On moved past the cursor in each shared account, oldest first, a few per run: fetch
 * each lead's owner changes and record who actually put it there (accountability.ts). Assigned On is
 * also restamped after calls on these leads, so a lead can come round again; its row is then refreshed.
 */
export async function runArrivals(env: Env): Promise<number> {
  const st = (await getState(env.DB, "arrivals")) ?? defaultState();
  const accounts = (await env.DB.prepare("SELECT id, name FROM users WHERE team = ?").bind(SHARED_TEAM)
    .all<{ id: string; name: string }>()).results;
  if (!accounts.length) return runUsers(env);   // users last refreshed before shared accounts were recorded
  const fresh: { lead: Activity; account: { id: string; name: string }; on: Date }[] = [];
  for (const account of accounts) {
    const rows = await leadsGet(env, { lookup: "OwnerId", value: account.id, page: 1, size: 100,
      columns: ["ProspectID", "CreatedOn", "CreatedByName", "mx_Assigned_By", "mx_Assigned_On"],
      sortBy: "mx_Assigned_On", desc: true });
    for (const lead of rows) {
      const on = parseUtc(lead.mx_Assigned_On);
      if (on && on > st.cursor) fresh.push({ lead, account, on });
    }
  }
  fresh.sort((a, b) => a.on.getTime() - b.on.getTime());
  let batch = fresh.slice(0, num(env.ARRIVAL_LEADS_PER_RUN, 8));
  const lastOn = batch[batch.length - 1]?.on;
  if (lastOn) batch = fresh.filter((r) => r.on <= lastOn);   // keep same-timestamp leads together
  const shared = sharedNames(env.SHARED_ACCOUNTS);
  const now = str(fmtUtc(new Date()));
  const values: string[][] = [];
  for (const r of batch) {
    const a = resolveArrival(r.lead, await leadActivities(env, String(r.lead.ProspectID), LEAD_ASSIGNED), r.account.name, shared);
    if (!a) continue;
    values.push([str(a.leadId), str(fmtUtc(a.at)), str(istDay(a.at)), str(r.account.id), str(a.how), str(a.login),
      str(a.putBy), str(a.status), str(a.assignedByField), str(a.possible), str(a.putBy), str(a.status), now]);
  }
  return writeBatch(env.DB, [
    ...multiInsert("account_arrival", ["lead_id", "at", "day", "account_id", "how", "login", "put_by", "status",
      "assigned_by_field", "possible", "first_put_by", "first_status", "updated_at"], values,
      "ON CONFLICT(lead_id, at) DO UPDATE SET put_by = excluded.put_by, status = excluded.status, " +
      "assigned_by_field = excluded.assigned_by_field, possible = excluded.possible, updated_at = excluded.updated_at " +
      "WHERE excluded.put_by <> account_arrival.put_by OR excluded.status <> account_arrival.status"),
    stateSql("arrivals", { cursor: lastOn ?? st.cursor, page: 1 }),
  ]);
}

/** Daily: team membership for every user in a team (shared admin accounts as their own "team"), and
 *  pruning of the attribution table. */
export async function runUsers(env: Env): Promise<number> {
  const now = fmtUtc(new Date());
  const shared = sharedNames(env.SHARED_ACCOUNTS);
  const users = (await getUsers(env))
    .map((u) => {
      const name = `${u.FirstName ?? ""} ${u.LastName ?? ""}`.trim();
      return { id: String(u.ID ?? ""), name,
               team: shared.has(name) ? SHARED_TEAM : teamFromGroups(u.MemberOfGroups) };
    })
    .filter((u) => u.id && u.team);
  return writeBatch(env.DB, [
    ...multiInsert("users", ["id", "name", "team", "updated_at"],
      users.map((u) => [str(u.id), str(u.name), str(u.team), str(now)]),
      "ON CONFLICT(id) DO UPDATE SET name = excluded.name, team = excluded.team, updated_at = excluded.updated_at"),
    `DELETE FROM lead_last_call WHERE at < ${str(fmtUtc(new Date(Date.now() - 3 * 24 * 60 * MIN)))}`,
    `DELETE FROM summary_cache WHERE computed_at < ${str(fmtUtc(new Date(Date.now() - 24 * 60 * MIN)))}`,
    ...["write_budget", "read_budget"].map((t) =>
      `DELETE FROM ${t} WHERE day < ${str(new Date(Date.now() - 35 * 24 * 60 * MIN).toISOString().slice(0, 10))}`),
    stateSql("users", { cursor: new Date(), page: 1 }),
  ]);
}

/** Users refresh every USERS_EVERY_HOURS, and again on the next leads slot after a failed run:
 *  recordError leaves an old cursor behind, which would otherwise hold the team list back for hours. */
export async function usersDue(db: D1Database): Promise<boolean> {
  const r = await db.prepare("SELECT cursor, last_error FROM sync_state WHERE task = 'users'")
    .first<{ cursor: string; last_error: string | null }>();
  const cursor = r ? parseUtc(r.cursor) : null;
  return !r || !cursor || r.last_error !== null || Date.now() - cursor.getTime() > USERS_EVERY_HOURS * 60 * MIN;
}

/** Cron rotation (every minute): even minutes = outbound calls; odd minutes rotate the rest, except
 *  :15 and :45 past each hour, which check shared-account arrivals. */
const ODD: Task[] = ["calls_in", "leads", "zip", "enroll"];

export function taskForMinute(minuteIndex: number): Task {
  if (minuteIndex % 30 === 15) return "arrivals";
  return minuteIndex % 2 === 0 ? "calls_out" : ODD[Math.floor(minuteIndex / 2) % ODD.length];
}

export async function runTask(raw: Env, task: Task): Promise<{ task: Task; rows: number; rowsRead: number; skipped?: string }> {
  const tally = { rowsRead: 0 };
  const env: Env = { ...raw, DB: countReads(raw.DB, tally) };
  const budget = num(env.WRITE_BUDGET, 90_000);
  if ((await rowsWrittenToday(env.DB)) >= budget) return { task, rows: 0, rowsRead: tally.rowsRead, skipped: "daily write budget reached" };
  if (task === "leads" && (await usersDue(env.DB))) task = "users";
  try {
    const rows = await ({
      calls_out: () => runCalls(env, "calls_out"),
      calls_in: () => runCalls(env, "calls_in"),
      leads: () => runLeads(env),
      zip: () => runZip(env),
      enroll: () => runEnroll(env),
      users: () => runUsers(env),
      arrivals: () => runArrivals(env),
    }[task])();
    await addToBudgets(raw.DB, rows, tally.rowsRead);
    return { task, rows, rowsRead: tally.rowsRead };
  } catch (err) {
    await recordError(env.DB, task, err);
    await addToBudgets(raw.DB, 1, tally.rowsRead).catch(() => {});
    throw err;
  }
}
