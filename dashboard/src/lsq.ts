// LeadSquared API access and record parsing (ported from integrations/leadsquared/client.py).
// No retries here: a failed run leaves its cursor untouched and the next cron run retries.

export interface LsqEnv {
  LEADSQUARED_HOST: string;
  LEADSQUARED_ACCESS_KEY: string;
  LEADSQUARED_SECRET_KEY: string;
}

export type Activity = Record<string, any>;

export const PHONE_INBOUND = 21;
export const PHONE_OUTBOUND = 22;
export const ZIP_NOTES = 237;
export const LEAD_ASSIGNED = 3001;
export const STAGE_CHANGE = 3002;
export const ENROLLED = "Course Enrolled";

const IST_MS = 330 * 60 * 1000;

/** ``https://<host>/v2/`` from a bare host or a full base URL. */
export function normalizeHost(host: string): string {
  let h = host.trim().replace(/\/+$/, "");
  if (!h.includes("://")) h = "https://" + h;
  if (!h.endsWith("/v2")) h += "/v2";
  return h + "/";
}

/** LeadSquared's UTC 'YYYY-MM-DD HH:MM:SS'. */
export function fmtUtc(d: Date): string {
  return d.toISOString().slice(0, 19).replace("T", " ");
}

export function parseUtc(s: string | null | undefined): Date | null {
  if (!s) return null;
  const d = new Date(s.slice(0, 19).replace(" ", "T") + "Z");
  return Number.isNaN(d.getTime()) ? null : d;
}

export function istDay(d: Date): string {
  return new Date(d.getTime() + IST_MS).toISOString().slice(0, 10);
}

export function istHour(d: Date): number {
  return new Date(d.getTime() + IST_MS).getUTCHours();
}

/** Split ``Key{=}Value{next}...``; repeated keys keep the last non-empty value. */
export function parseNote(note: string | null | undefined): Record<string, string> {
  const out: Record<string, string> = {};
  for (const part of (note ?? "").split("{next}")) {
    const i = part.indexOf("{=}");
    if (i < 0) continue;
    const key = part.slice(0, i);
    const value = part.slice(i + 3);
    if (value || !(key in out)) out[key] = value;
  }
  return out;
}

export interface Call {
  id: string;
  leadId: string;
  dir: "in" | "out";
  start: Date;
  userId: string;
  caller: string;
  status: string;   // Answered / NotAnswered / CallFailure (out); Answered / Missed (in)
  duration: number; // seconds
}

export function parseCall(a: Activity): Call | null {
  const start = parseUtc(a.CreatedOn);
  if (!start) return null;
  const note = parseNote(a.ActivityEvent_Note);
  const duration = Math.trunc(Number.parseFloat(note.Duration || "0")) || 0;
  return {
    id: String(a.ProspectActivityId ?? a.Id ?? ""),
    leadId: String(a.RelatedProspectId ?? ""),
    dir: Number(a.ActivityEvent) === PHONE_INBOUND ? "in" : "out",
    start,
    userId: String(note.UserId || a.Owner || a.CreatedBy || ""),
    caller: String(note.Caller || a.CreatedByName || "").trim(),
    status: String(note.Status || a.Status || ""),
    duration,
  };
}

/** Zipteams pass/fail score: '{"mx_CustomObject_1":"100"}' or a bare number. */
export function zipScore(raw: unknown): number | null {
  if (raw === null || raw === undefined || raw === "") return null;
  try {
    const v = JSON.parse(String(raw));
    if (v && typeof v === "object") {
      const x = (v as Record<string, unknown>).mx_CustomObject_1;
      return x === undefined || x === null || x === "" ? null : Number(x);
    }
    return Number.isFinite(Number(v)) ? Number(v) : null;
  } catch {
    const m = String(raw).match(/\d+(\.\d+)?/);
    return m ? Number(m[0]) : null;
  }
}

export class LsqError extends Error {
  constructor(message: string, readonly status?: number) {
    super(message);
  }
}

export async function lsq(
  env: LsqEnv,
  method: "GET" | "POST",
  path: string,
  body?: unknown,
  params: Record<string, string> = {},
): Promise<any> {
  const url = new URL(normalizeHost(env.LEADSQUARED_HOST) + path);
  url.searchParams.set("accessKey", env.LEADSQUARED_ACCESS_KEY);
  url.searchParams.set("secretKey", env.LEADSQUARED_SECRET_KEY);
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  const resp = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await resp.text();
  let data: any = text;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    /* non-JSON error body */
  }
  if (!resp.ok || (data && typeof data === "object" && data.Status === "Error")) {
    const msg = data && typeof data === "object" ? data.ExceptionMessage ?? JSON.stringify(data) : String(data);
    throw new LsqError(`${method} ${path} -> HTTP ${resp.status}: ${String(msg).slice(0, 300)}`, resp.status);
  }
  return data;
}

/** One page of activities of one type created in [from, to] across all leads. */
export async function activitiesByEvent(
  env: LsqEnv, event: number, from: Date, to: Date, pageIndex: number, pageSize: number,
): Promise<Activity[]> {
  const data = await lsq(env, "POST", "ProspectActivity.svc/CustomActivity/RetrieveByActivityEvent", {
    Parameter: { FromDate: fmtUtc(from), ToDate: fmtUtc(to), ActivityEvent: event },
    Paging: { PageIndex: pageIndex, PageSize: pageSize },
  });
  return (data && data.List) || [];
}

export async function leadsGet(
  env: LsqEnv,
  opts: { lookup: string; value: string; op?: string; columns: string[]; page: number; size: number;
          sortBy: string; desc: boolean },
): Promise<Activity[]> {
  const data = await lsq(env, "POST", "LeadManagement.svc/Leads.Get", {
    Parameter: { LookupName: opts.lookup, LookupValue: opts.value, SqlOperator: opts.op ?? "=" },
    Columns: { Include_CSV: opts.columns.join(",") },
    Paging: { PageIndex: opts.page, PageSize: opts.size },
    Sorting: { ColumnName: opts.sortBy, Direction: opts.desc ? "1" : "0" },
  });
  return Array.isArray(data) ? data : [];
}

/** A lead's activities of one type, newest first (up to 100). */
export async function leadActivities(env: LsqEnv, leadId: string, event: number): Promise<Activity[]> {
  const data = await lsq(env, "POST", "ProspectActivity.svc/Retrieve",
    { Parameter: { ActivityEvent: event }, Paging: { Offset: 0, RowCount: 100 } }, { leadId });
  return (data && data.ProspectActivities) || [];
}

/** A lead's stage changes (newest first). */
export const stageChanges = (env: LsqEnv, leadId: string) => leadActivities(env, leadId, STAGE_CHANGE);

export async function getUsers(env: LsqEnv): Promise<Activity[]> {
  const data = await lsq(env, "GET", "UserManagement.svc/Users.Get");
  return Array.isArray(data) ? data : [];
}

/** The Data pairs of a system activity: {PreviousStage, CurrentStage, CreatedBy, Comment} for a stage change,
 *  {PreviousOwner, CurrentOwner, CreatedBy} for an owner change. */
export function stageData(a: Activity): Record<string, string> {
  const out: Record<string, string> = {};
  for (const d of a.Data ?? []) out[d.Key] = d.Value;
  return out;
}
