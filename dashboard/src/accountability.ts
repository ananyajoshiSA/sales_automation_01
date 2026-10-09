// Who actually put a lead into a shared admin account (e.g. Rinku Jhala's), never the account itself.
// Same rules as analytics/accountability.py (docs/accountability.md): the owner-change log names the
// login that made each change. A personal login is the person (Verified). A shared login is never the
// person: the lead's "Assigned By" field names them only if it belongs to this change (it is the latest
// owner change, Assigned On was stamped at or after it, and the named person made no earlier change on
// the lead); otherwise Unverified, with the field's name kept as a lead to check. 'System' is automation.
import { Activity, parseUtc, stageData } from "./lsq";

export const SHARED_TEAM = "(shared account)";
export const DEFAULT_SHARED = "Rinku Jhala,Admin";
const AUTOMATION = new Set(["System"]);
const ASSIGNED_ON_SLACK_MS = 5 * 60_000;

export const VERIFIED = "Verified", ASSIGNED_BY = "Verified (Assigned By)", UNVERIFIED = "Unverified",
  AUTOMATED = "Automated";

export function sharedNames(csv: string | undefined): Set<string> {
  return new Set((csv ?? DEFAULT_SHARED).split(",").map((s) => s.trim()).filter(Boolean));
}

type Kind = "blank" | "automation" | "shared" | "person";

function kind(name: string, shared: Set<string>): Kind {
  if (!name) return "blank";
  if (AUTOMATION.has(name)) return "automation";
  return shared.has(name) ? "shared" : "person";
}

export interface Arrival {
  leadId: string; at: Date; how: "owner change" | "created there"; login: string; putBy: string;
  status: string; assignedByField: string; possible: string;
}

/** How the lead came to sit in `account` (its latest owner change into it, else its creation), and by whom. */
export function resolveArrival(lead: Activity, assigned: Activity[], account: string, shared: Set<string>): Arrival | null {
  const changes = assigned
    .map((a) => { const d = stageData(a); return { t: parseUtc(a.CreatedOn), to: (d.CurrentOwner ?? "").trim(), by: (d.CreatedBy ?? "").trim() }; })
    .filter((c): c is { t: Date; to: string; by: string } => c.t !== null)
    .sort((a, b) => a.t.getTime() - b.t.getTime());
  const field = String(lead.mx_Assigned_By ?? "").trim();
  const leadId = String(lead.ProspectID ?? "");
  const into = changes.filter((c) => c.to === account);
  const last = into[into.length - 1];
  if (!last) {
    const at = parseUtc(lead.CreatedOn);
    if (!at) return null;
    const creator = String(lead.CreatedByName ?? "").trim();
    const k = kind(creator, shared);
    return { leadId, at, how: "created there", login: creator, assignedByField: field, possible: "",
      putBy: k === "person" ? creator : k === "automation" ? "System (automation)" : "Unverified",
      status: k === "person" ? VERIFIED : k === "automation" ? AUTOMATED : UNVERIFIED };
  }
  const base = { leadId, at: last.t, how: "owner change" as const, login: last.by, assignedByField: field };
  const k = kind(last.by, shared);
  if (k === "person") return { ...base, putBy: last.by, status: VERIFIED, possible: "" };
  if (k === "automation") return { ...base, putBy: "System (automation)", status: AUTOMATED, possible: "" };
  if (kind(field, shared) !== "person") return { ...base, putBy: "Unverified", status: UNVERIFIED, possible: "" };
  const isLatest = changes[changes.length - 1].t.getTime() === last.t.getTime();
  const stamped = parseUtc(lead.mx_Assigned_On);
  const earlier = changes.some((c) => c.t < last.t && c.by === field);
  if (isLatest && stamped && stamped.getTime() >= last.t.getTime() - ASSIGNED_ON_SLACK_MS && !earlier) {
    return { ...base, putBy: field, status: ASSIGNED_BY, possible: "" };
  }
  return { ...base, putBy: "Unverified", status: UNVERIFIED, possible: field };
}
