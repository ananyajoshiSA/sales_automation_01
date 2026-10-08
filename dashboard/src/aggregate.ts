// Pure aggregation: turn one batch of LeadSquared records into daily-total deltas.
import { Activity, Call, ENROLLED, istDay, istHour, parseUtc, stageData, zipScore } from "./lsq";

export const REAL_CALL_SECS = 120;

export interface CallerDelta {
  day: string; userId: string; name: string;
  dials: number; answered: number; notAnswered: number; failures: number;
  realCalls: number; talkSecs: number; inbound: number; inboundMissed: number;
}

export interface HourDelta { day: string; hour: number; dials: number; answered: number }

export interface CallBatch {
  callers: CallerDelta[];
  hours: HourDelta[];
  lastCall: { leadId: string; userId: string; at: Date }[];
}

export function aggregateCalls(calls: Call[]): CallBatch {
  const callers = new Map<string, CallerDelta>();
  const hours = new Map<string, HourDelta>();
  const last = new Map<string, { leadId: string; userId: string; at: Date }>();
  const seen = new Set<string>();
  for (const c of calls) {
    if (!c.userId || (c.id && seen.has(c.id))) continue;
    if (c.id) seen.add(c.id);
    const day = istDay(c.start);
    const key = `${day}|${c.userId}`;
    let d = callers.get(key);
    if (!d) {
      d = { day, userId: c.userId, name: c.caller, dials: 0, answered: 0, notAnswered: 0, failures: 0,
            realCalls: 0, talkSecs: 0, inbound: 0, inboundMissed: 0 };
      callers.set(key, d);
    }
    if (c.caller) d.name = c.caller;
    const answered = c.status === "Answered";
    if (c.dir === "out") {
      d.dials++;
      if (answered) {
        d.answered++;
        d.talkSecs += c.duration;
        if (c.duration >= REAL_CALL_SECS) d.realCalls++;
      } else if (c.status === "NotAnswered") d.notAnswered++;
      else if (c.status === "CallFailure") d.failures++;
      const hk = `${day}|${istHour(c.start)}`;
      const h = hours.get(hk) ?? { day, hour: istHour(c.start), dials: 0, answered: 0 };
      h.dials++;
      if (answered) h.answered++;
      hours.set(hk, h);
    } else {
      d.inbound++;
      if (!answered) d.inboundMissed++;
    }
    if (answered && c.leadId) {
      const prev = last.get(c.leadId);
      if (!prev || prev.at < c.start) last.set(c.leadId, { leadId: c.leadId, userId: c.userId, at: c.start });
    }
  }
  return { callers: [...callers.values()], hours: [...hours.values()], lastCall: [...last.values()] };
}

export interface LeadDelta { day: string; source: string; ownerId: string; n: number }

export function aggregateLeads(leads: Activity[]): LeadDelta[] {
  const out = new Map<string, LeadDelta>();
  for (const l of leads) {
    const t = parseUtc(l.CreatedOn);
    if (!t) continue;
    const day = istDay(t);
    const source = String(l.Source || "(blank)").slice(0, 80);
    const ownerId = String(l.OwnerId || "");
    const k = `${day}|${source}|${ownerId}`;
    const d = out.get(k) ?? { day, source, ownerId, n: 0 };
    d.n++;
    out.set(k, d);
  }
  return [...out.values()];
}

export interface ZipDelta {
  day: string; userId: string; analysed: number;
  pitchN: number; pitchSum: number; probeN: number; probeSum: number; objN: number; objSum: number;
  intentRated: number; intentHigh: number; intentModerate: number; intentLow: number;
}

/** Zipteams notes (activity 237) -> per caller per day, attributed via the lead's last answered call. */
export function aggregateZip(notes: Activity[], callerOfLead: Map<string, string>): ZipDelta[] {
  const out = new Map<string, ZipDelta>();
  for (const a of notes) {
    const t = parseUtc(a.CreatedOn);
    if (!t) continue;
    const day = istDay(t);
    const userId = callerOfLead.get(String(a.RelatedProspectId ?? "")) ?? "";
    const k = `${day}|${userId}`;
    const d = out.get(k) ?? { day, userId, analysed: 0, pitchN: 0, pitchSum: 0, probeN: 0, probeSum: 0,
                              objN: 0, objSum: 0, intentRated: 0, intentHigh: 0, intentModerate: 0, intentLow: 0 };
    d.analysed++;
    const pitch = zipScore(a.mx_Custom_4), probe = zipScore(a.mx_Custom_5), obj = zipScore(a.mx_Custom_6);
    if (pitch !== null) { d.pitchN++; d.pitchSum += pitch; }
    if (probe !== null) { d.probeN++; d.probeSum += probe; }
    if (obj !== null) { d.objN++; d.objSum += obj; }
    const intent = String(a.mx_Custom_1 || "").toUpperCase();
    if (intent && intent !== "NOT_AVAILABLE" && intent !== "UNKNOWN") {
      d.intentRated++;
      if (intent === "HIGH") d.intentHigh++;
      else if (intent === "MODERATE") d.intentModerate++;
      else if (intent === "LOW") d.intentLow++;
    }
    out.set(k, d);
  }
  return [...out.values()];
}

/** The lead's FIRST ever move to 'Course Enrolled' (so re-tagged existing students are ignored). */
export function firstEnrollment(changes: Activity[]): { at: Date; setBy: string } | null {
  let best: { at: Date; setBy: string } | null = null;
  for (const a of changes) {
    const d = stageData(a);
    const t = parseUtc(a.CreatedOn);
    if (!t || d.CurrentStage !== ENROLLED) continue;
    if (!best || t < best.at) best = { at: t, setBy: (d.CreatedBy || "").trim() };
  }
  return best;
}
