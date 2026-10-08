import { describe, expect, it } from "vitest";
import { aggregateCalls, aggregateLeads, aggregateZip, firstEnrollment } from "../src/aggregate";
import { istDay, istHour, normalizeHost, parseCall, parseNote, zipScore } from "../src/lsq";
import { addOnConflict, multiInsert, str } from "../src/sql";
import { taskForMinute } from "../src/tasks";

const call = (id: string, ev: number, createdOn: string, note: Record<string, string>, lead = "L1") => ({
  ProspectActivityId: id, RelatedProspectId: lead, ActivityEvent: ev, CreatedOn: createdOn,
  ActivityEvent_Note: Object.entries(note).map(([k, v]) => `${k}{=}${v}`).join("{next}"),
});

describe("parsing", () => {
  it("parses activity notes, keeping the last non-empty value", () => {
    expect(parseNote("A{=}1{next}B{=}{next}A{=}2{next}junk")).toEqual({ A: "2", B: "" });
    expect(parseNote(null)).toEqual({});
  });

  it("parses a phone call activity", () => {
    const c = parseCall(call("a1", 22, "2026-10-07 06:34:54.000",
      { UserId: "u1", Caller: " Asha ", Status: "Answered", Duration: "150.0" }))!;
    expect(c).toMatchObject({ id: "a1", leadId: "L1", dir: "out", userId: "u1", caller: "Asha", status: "Answered", duration: 150 });
    expect(istDay(c.start)).toBe("2026-10-07");
    expect(istHour(c.start)).toBe(12);
  });

  it("reads Zipteams scores in either format", () => {
    expect(zipScore('{"mx_CustomObject_1":"100"}')).toBe(100);
    expect(zipScore('{"mx_CustomObject_1":""}')).toBeNull();
    expect(zipScore("0")).toBe(0);
    expect(zipScore(undefined)).toBeNull();
  });

  it("normalizes a bare LeadSquared host", () => {
    expect(normalizeHost("api-in21.leadsquared.com")).toBe("https://api-in21.leadsquared.com/v2/");
    expect(normalizeHost("https://api-in21.leadsquared.com/v2/")).toBe("https://api-in21.leadsquared.com/v2/");
  });
});

describe("aggregation", () => {
  it("rolls calls up per caller/day and hour, ignoring duplicates", () => {
    const calls = [
      call("1", 22, "2026-10-07 06:00:00", { UserId: "u1", Caller: "Asha", Status: "Answered", Duration: "200" }),
      call("1", 22, "2026-10-07 06:00:00", { UserId: "u1", Caller: "Asha", Status: "Answered", Duration: "200" }),
      call("2", 22, "2026-10-07 06:01:00", { UserId: "u1", Status: "NotAnswered", Duration: "0" }, "L2"),
      call("3", 22, "2026-10-07 06:02:00", { UserId: "u1", Status: "CallFailure", Duration: "0" }, "L3"),
      call("4", 21, "2026-10-07 06:03:00", { UserId: "u1", Status: "Missed", Duration: "0" }, "L4"),
      call("5", 22, "2026-10-07 19:00:00", { UserId: "u1", Status: "Answered", Duration: "30" }),  // next IST day
    ].map(parseCall).filter((c) => c !== null);
    const b = aggregateCalls(calls);
    const d1 = b.callers.find((c) => c.day === "2026-10-07")!;
    expect(d1).toMatchObject({ name: "Asha", dials: 3, answered: 1, notAnswered: 1, failures: 1, realCalls: 1,
      talkSecs: 200, inbound: 1, inboundMissed: 1 });
    expect(b.callers.find((c) => c.day === "2026-10-08")).toMatchObject({ dials: 1, answered: 1, realCalls: 0 });
    expect(b.hours.find((h) => h.day === "2026-10-07")).toEqual({ day: "2026-10-07", hour: 11, dials: 3, answered: 1 });
    expect(b.lastCall).toHaveLength(1);   // only answered calls; L1's latest is the 19:00 UTC call
    expect(b.lastCall[0].at.toISOString()).toBe("2026-10-07T19:00:00.000Z");
  });

  it("counts leads per IST day, source and owner", () => {
    const d = aggregateLeads([
      { CreatedOn: "2026-10-06 01:10:00", Source: "Women AI", OwnerId: "o1" },
      { CreatedOn: "2026-10-06 01:11:00", Source: "Women AI", OwnerId: "o1" },
      { CreatedOn: "2026-10-06 20:00:00", Source: "", OwnerId: "" },
    ]);
    expect(d).toContainEqual({ day: "2026-10-06", source: "Women AI", ownerId: "o1", n: 2 });
    expect(d).toContainEqual({ day: "2026-10-07", source: "(blank)", ownerId: "", n: 1 });
  });

  it("attributes Zipteams notes to the lead's last caller", () => {
    const z = aggregateZip([
      { CreatedOn: "2026-10-07 06:10:00", RelatedProspectId: "L1", mx_Custom_1: "high",
        mx_Custom_4: '{"mx_CustomObject_1":"100"}', mx_Custom_5: '{"mx_CustomObject_1":"0"}', mx_Custom_6: "" },
      { CreatedOn: "2026-10-07 06:11:00", RelatedProspectId: "L9", mx_Custom_1: "NOT_AVAILABLE" },
    ], new Map([["L1", "u1"]]));
    expect(z.find((d) => d.userId === "u1")).toMatchObject({ analysed: 1, pitchN: 1, pitchSum: 100, probeN: 1,
      probeSum: 0, objN: 0, intentRated: 1, intentHigh: 1 });
    expect(z.find((d) => d.userId === "")).toMatchObject({ analysed: 1, intentRated: 0 });
  });

  it("dates an enrollment by the FIRST move to Course Enrolled", () => {
    const sc = (on: string, cur: string, by = "X") =>
      ({ CreatedOn: on, Data: [{ Key: "CurrentStage", Value: cur }, { Key: "CreatedBy", Value: by }] });
    const first = firstEnrollment([sc("2026-10-08 09:19:32", "Course Enrolled", "Priya"),
      sc("2026-08-12 14:30:51", "Opportunity Created"), sc("2025-11-23 11:43:00", "Course Enrolled", "Ravi")]);
    expect(first).toEqual({ at: new Date("2025-11-23T11:43:00Z"), setBy: "Ravi" });
    expect(firstEnrollment([sc("2026-10-01 00:00:00", "New Lead")])).toBeNull();
  });
});

describe("sql", () => {
  it("escapes quotes and splits large inserts", () => {
    expect(str("O'Brien")).toBe("'O''Brien'");
    const rows = Array.from({ length: 3000 }, (_, i) => [str(`user-${i}-${"x".repeat(40)}`), String(i)]);
    const stmts = multiInsert("t", ["a", "b"], rows, addOnConflict(["a"], ["b"]));
    expect(stmts.length).toBeGreaterThan(1);
    for (const s of stmts) {
      expect(s.length).toBeLessThan(100_000);
      expect(s.endsWith("ON CONFLICT(a) DO UPDATE SET b = b + excluded.b")).toBe(true);
    }
    expect(multiInsert("t", ["a"], [])).toEqual([]);
  });
});

describe("cron rotation", () => {
  it("gives outbound calls every even minute and rotates the rest", () => {
    expect([0, 1, 2, 3, 4, 5, 6, 7, 8, 9].map(taskForMinute)).toEqual([
      "calls_out", "calls_in", "calls_out", "leads", "calls_out", "zip", "calls_out", "enroll", "calls_out", "calls_in",
    ]);
  });
});
