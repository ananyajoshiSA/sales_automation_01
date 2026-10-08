// Read side: turn daily totals into the dashboard summary. Definitions match the Python report
// (exports/insights_*): a caller's working day = >= 20 dials; lagging = >= 2 flags vs team median
// (70% threshold), at least one of them effort/conversations, >= 2 working days, no dialer issue.
import { fmtUtc, istDay, istHour, parseUtc } from "./lsq";

export const ACTIVE_DAY_DIALS = 20;
export const LAG_RATIO = 0.7;
export const DIALER_FAILURE_PCT = 50;
const MIN_TEAM_SIZE = 3;

export interface CallerDayRow {
  day: string; user_id: string; name: string; dials: number; answered: number; not_answered: number;
  failures: number; real_calls: number; talk_secs: number; inbound: number; inbound_missed: number;
}

export interface CallerStat {
  userId: string; caller: string; team: string; activeDays: number; dials: number;
  dialsPerDay: number; connectPct: number; realPerDay: number; talkMinPerDay: number; failurePct: number;
  inbound: number; inboundMissedPct: number; conversions: number;
  flags: string[]; status: "lagging" | "watch" | "ok" | "one_day" | "dialer_issue"; benchmark: string;
}

const pct = (a: number, b: number) => (b ? Math.round((1000 * a) / b) / 10 : 0);
const r1 = (x: number) => Math.round(x * 10) / 10;

export function median(xs: number[]): number {
  const s = xs.filter((x) => Number.isFinite(x)).sort((a, b) => a - b);
  if (!s.length) return 0;
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export function computeCallers(rows: CallerDayRow[], teamOf: Map<string, string>,
                               conversions: Map<string, number>): CallerStat[] {
  const by = new Map<string, CallerDayRow[]>();
  for (const r of rows) by.set(r.user_id, [...(by.get(r.user_id) ?? []), r]);
  const stats: CallerStat[] = [];
  for (const [userId, days] of by) {
    const active = days.filter((d) => d.dials >= ACTIVE_DAY_DIALS).length;
    if (!active) continue;
    const t = days.reduce((a, d) => ({
      dials: a.dials + d.dials, answered: a.answered + d.answered, failures: a.failures + d.failures,
      real: a.real + d.real_calls, talk: a.talk + d.talk_secs, inbound: a.inbound + d.inbound,
      missed: a.missed + d.inbound_missed,
    }), { dials: 0, answered: 0, failures: 0, real: 0, talk: 0, inbound: 0, missed: 0 });
    stats.push({
      userId, caller: days[days.length - 1].name || userId, team: teamOf.get(userId) || "(no team)",
      activeDays: active, dials: t.dials, dialsPerDay: r1(t.dials / active), connectPct: pct(t.answered, t.dials),
      realPerDay: r1(t.real / active), talkMinPerDay: r1(t.talk / 60 / active), failurePct: pct(t.failures, t.dials),
      inbound: t.inbound, inboundMissedPct: pct(t.missed, t.inbound), conversions: conversions.get(userId) ?? 0,
      flags: [], status: "ok", benchmark: "",
    });
  }
  const byTeam = new Map<string, CallerStat[]>();
  for (const s of stats) byTeam.set(s.team, [...(byTeam.get(s.team) ?? []), s]);
  const metrics: [keyof CallerStat, string][] = [
    ["dialsPerDay", "low dialing"], ["realPerDay", "few real conversations"],
    ["talkMinPerDay", "low talk time"], ["connectPct", "low connect rate"],
  ];
  for (const s of stats) {
    const team = byTeam.get(s.team) ?? [];
    const peers = team.length >= MIN_TEAM_SIZE ? team : stats;
    s.benchmark = peers === stats ? "account" : s.team;
    for (const [m, label] of metrics) {
      const med = median(peers.map((p) => p[m] as number));
      if (med && (s[m] as number) < LAG_RATIO * med) s.flags.push(label);
    }
    if (s.conversions === 0 && median(peers.map((p) => p.conversions)) >= 1) s.flags.push("no conversions (peers converting)");
    const productivity = s.flags.some((f) => ["low dialing", "few real conversations", "low talk time"].includes(f));
    s.status = s.failurePct >= DIALER_FAILURE_PCT ? "dialer_issue"
      : s.activeDays < 2 ? "one_day"
      : s.flags.length >= 2 && productivity ? "lagging"
      : s.flags.length ? "watch" : "ok";
  }
  const order = { lagging: 0, dialer_issue: 1, watch: 2, one_day: 3, ok: 4 };
  return stats.sort((a, b) => order[a.status] - order[b.status] || b.flags.length - a.flags.length
    || a.team.localeCompare(b.team) || a.dialsPerDay - b.dialsPerDay);
}

/** Data-gap alarm: last completed IST hour's dials vs the median of the same hour on the previous 7 days. */
export function dataGap(hours: { day: string; hour: number; dials: number }[], now: Date) {
  const today = istDay(now);
  const hour = istHour(now) - 1;
  if (hour < 10 || hour > 19) return { alarm: false, hour, dials: null as number | null, median: null as number | null };
  const at = (d: string) => hours.find((h) => h.day === d && h.hour === hour)?.dials ?? 0;
  const prev = [...new Set(hours.map((h) => h.day))].filter((d) => d < today).sort().slice(-7);
  const med = median(prev.map(at).filter((x) => x > 0));
  const dials = at(today);
  return { alarm: med >= 200 && dials < 0.3 * med, hour, dials, median: med };
}

function sum<T>(rows: T[], key: (r: T) => string, val: (r: T) => number): [string, number][] {
  const m = new Map<string, number>();
  for (const r of rows) m.set(key(r), (m.get(key(r)) ?? 0) + val(r));
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

export async function summary(db: D1Database, from: string, to: string, budgetLimit: number) {
  const now = new Date();
  const weekAgo = istDay(new Date(now.getTime() - 8 * 86_400_000));
  const q = <T>(sql: string, ...args: unknown[]) => db.prepare(sql).bind(...args).all<T>().then((r) => r.results);
  const [users, callerRows, enrolls, leadsByDay, leadsBySource, leadsByOwner, zip, hours, sync, budget] = await Promise.all([
    q<{ id: string; name: string; team: string }>("SELECT id, name, team FROM users"),
    q<CallerDayRow>("SELECT * FROM caller_day WHERE day BETWEEN ? AND ? ORDER BY day", from, to),
    q<{ lead_id: string; day: string; owner_id: string; set_by: string }>(
      "SELECT lead_id, day, owner_id, set_by FROM enrollment WHERE day BETWEEN ? AND ?", from, to),
    q<{ day: string; n: number }>("SELECT day, SUM(n) AS n FROM lead_day WHERE day BETWEEN ? AND ? GROUP BY day ORDER BY day", from, to),
    q<{ source: string; n: number }>(
      "SELECT source, SUM(n) AS n FROM lead_day WHERE day BETWEEN ? AND ? GROUP BY source ORDER BY n DESC LIMIT 12", from, to),
    q<{ owner_id: string; n: number }>("SELECT owner_id, SUM(n) AS n FROM lead_day WHERE day BETWEEN ? AND ? GROUP BY owner_id", from, to),
    q<Record<string, any>>("SELECT * FROM zip_day WHERE day BETWEEN ? AND ?", from, to),
    q<{ day: string; hour: number; dials: number; answered: number }>("SELECT * FROM calls_hour WHERE day >= ?", weekAgo),
    q<{ task: string; cursor: string; updated_at: string; last_error: string | null }>("SELECT task, cursor, updated_at, last_error FROM sync_state"),
    q<{ rows: number }>("SELECT rows FROM write_budget WHERE day = ?", now.toISOString().slice(0, 10)),
  ]);

  const teamOf = new Map(users.map((u) => [u.id, u.team]));
  const nameOf = new Map(users.map((u) => [u.id, u.name]));
  for (const r of callerRows) if (r.name) nameOf.set(r.user_id, nameOf.get(r.user_id) ?? r.name);
  const conv = new Map<string, number>();
  for (const e of enrolls) conv.set(e.owner_id, (conv.get(e.owner_id) ?? 0) + 1);
  const callers = computeCallers(callerRows, teamOf, conv);
  const lagging = new Set(callers.filter((c) => c.status === "lagging").map((c) => c.userId));

  const totals = callerRows.reduce((a, r) => ({ dials: a.dials + r.dials, answered: a.answered + r.answered,
    real: a.real + r.real_calls, talk: a.talk + r.talk_secs, failures: a.failures + r.failures }),
    { dials: 0, answered: 0, real: 0, talk: 0, failures: 0 });

  const zipGroup = (rows: Record<string, any>[]) => {
    const t = rows.reduce((a, r) => { for (const k of Object.keys(a)) a[k] += r[k] ?? 0; return a; },
      { analysed: 0, pitch_n: 0, pitch_sum: 0, probe_n: 0, probe_sum: 0, obj_n: 0, obj_sum: 0,
        intent_rated: 0, intent_high: 0, intent_moderate: 0 } as Record<string, number>);
    return { analysed: t.analysed, pitchPct: t.pitch_n ? Math.round(t.pitch_sum / t.pitch_n) : null,
      probePct: t.probe_n ? Math.round(t.probe_sum / t.probe_n) : null,
      objectionPct: t.obj_n ? Math.round(t.obj_sum / t.obj_n) : null,
      highOrModerateIntentPct: pct(t.intent_high + t.intent_moderate, t.intent_rated) };
  };

  const teams = sum(callers, (c) => c.team, () => 1).map(([team, n]) => {
    const cs = callers.filter((c) => c.team === team);
    return { team, callers: n, lagging: cs.filter((c) => c.status === "lagging").length,
      medDialsPerDay: median(cs.map((c) => c.dialsPerDay)), medRealPerDay: median(cs.map((c) => c.realPerDay)),
      medTalkMinPerDay: median(cs.map((c) => c.talkMinPerDay)), medConnectPct: median(cs.map((c) => c.connectPct)),
      conversions: cs.reduce((a, c) => a + c.conversions, 0) };
  });

  const lagMin = (c: string) => Math.round((now.getTime() - (parseUtc(c)?.getTime() ?? 0)) / 60_000);
  return {
    range: { from, to }, generatedAt: fmtUtc(now) + " UTC",
    leads: {
      total: leadsByDay.reduce((a, r) => a + r.n, 0), byDay: leadsByDay, bySource: leadsBySource,
      byTeam: sum(leadsByOwner, (r) => teamOf.get(r.owner_id) || "(no team)", (r) => r.n).slice(0, 15),
    },
    enrollments: {
      total: enrolls.length,
      byDay: sum(enrolls, (e) => e.day, () => 1).sort((a, b) => a[0].localeCompare(b[0])),
      byTeam: sum(enrolls, (e) => teamOf.get(e.owner_id) || "(no team)", () => 1),
      byOwner: sum(enrolls, (e) => nameOf.get(e.owner_id) || e.owner_id || "(unassigned)", () => 1).slice(0, 15),
    },
    calls: { dials: totals.dials, connectPct: pct(totals.answered, totals.dials), realCalls: totals.real,
      talkHours: Math.round(totals.talk / 360) / 10, failurePct: pct(totals.failures, totals.dials) },
    counts: {
      callers: callers.length,
      lagging: lagging.size,
      watch: callers.filter((c) => c.status === "watch").length,
      oneDay: callers.filter((c) => c.status === "one_day").length,
      dialerIssue: callers.filter((c) => c.status === "dialer_issue").length,
    },
    callers, teams,
    zip: { lagging: zipGroup(zip.filter((r) => lagging.has(r.user_id))),
           others: zipGroup(zip.filter((r) => !lagging.has(r.user_id) && r.user_id)) },
    // While the call sync is catching up, today's hours are incomplete, so the gap check can't judge.
    dataGap: (() => {
      const out = sync.find((x) => x.task === "calls_out");
      const behind = out ? lagMin(out.cursor) : Infinity;
      return behind > 30 ? { ...dataGap(hours, now), alarm: false, catchingUp: true } : { ...dataGap(hours, now), catchingUp: false };
    })(),
    // users/enroll cursors are "last refresh" / "last enrolled-lead edit seen", not ingestion lag.
    sync: sync.map((s) => ({ task: s.task, behindMin: ["users", "enroll"].includes(s.task) ? null : lagMin(s.cursor),
      lastSuccess: s.updated_at, lastError: s.last_error })),
    budget: { rowsWrittenToday: budget[0]?.rows ?? 0, limit: budgetLimit, freeTierLimit: 100_000 },
  };
}
