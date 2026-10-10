import { afterEach, describe, expect, it, vi } from "vitest";
import { SHARED_TEAM } from "../src/accountability";
import { CALLING_SOFTWARE, CallerDayRow, computeCallers, dataGap, median, teamFromGroups } from "../src/metrics";
import { runUsers } from "../src/tasks";
import type { Env } from "../src/tasks";
import { fakeD1 } from "./fake_d1.mjs";

const day = (d: string, user: string, dials: number, answered: number, real: number, talkMin: number, failures = 0): CallerDayRow =>
  ({ day: d, user_id: user, name: user, dials, answered, not_answered: dials - answered - failures, failures,
     real_calls: real, talk_secs: talkMin * 60, inbound: 0, inbound_missed: 0 });

const twoDays = (user: string, dials: number, answered: number, real: number, talkMin: number, failures = 0) =>
  [day("2026-10-06", user, dials, answered, real, talkMin, failures), day("2026-10-07", user, dials, answered, real, talkMin, failures)];

describe("lagging rule", () => {
  const team = new Map(["a", "b", "c", "d", "e", "f"].map((u) => [u, "Team X"]));

  it("flags a caller below 70% of the team median on two metrics incl. conversations", () => {
    const rows = [
      ...twoDays("a", 200, 60, 10, 120), ...twoDays("b", 200, 60, 10, 120), ...twoDays("c", 200, 60, 10, 120),
      ...twoDays("d", 190, 58, 3, 30),   // few real conversations + low talk time
      ...twoDays("e", 100, 60, 10, 120), // low dialing only -> watch
    ];
    const s = Object.fromEntries(computeCallers(rows, team, new Map()).map((c) => [c.userId, c]));
    expect(s.d.status).toBe("lagging");
    expect(s.d.flags).toEqual(["few real conversations", "low talk time"]);
    expect(s.e.status).toBe("watch");
    expect(s.a.status).toBe("ok");
    expect(s.a.benchmark).toBe("Team X");
  });

  it("separates dialer failures and one-day callers from lagging", () => {
    const rows = [
      ...twoDays("a", 200, 60, 10, 120), ...twoDays("b", 200, 60, 10, 120), ...twoDays("c", 200, 60, 10, 120),
      ...twoDays("f", 300, 0, 0, 0, 300),                // every dial fails at the telephony layer
      day("2026-10-07", "d", 30, 5, 0, 3),               // only one working day
      day("2026-10-07", "x", 5, 1, 0, 1),                // < 20 dials: not a working day, excluded
    ];
    const s = Object.fromEntries(computeCallers(rows, team, new Map()).map((c) => [c.userId, c]));
    expect(s.f.status).toBe("dialer_issue");
    expect(s.d.status).toBe("one_day");
    expect(s.x).toBeUndefined();
  });

  it("flags no conversions only when peers convert", () => {
    const rows = [...twoDays("a", 200, 60, 4, 50), ...twoDays("b", 200, 60, 10, 120), ...twoDays("c", 200, 60, 10, 120)];
    const conv = new Map([["b", 2], ["c", 1]]);
    const a = computeCallers(rows, team, conv).find((c) => c.userId === "a")!;
    expect(a.flags).toContain("no conversions (peers converting)");
    expect(a.status).toBe("lagging");
  });

  it("uses the account as benchmark for teams under 3 callers", () => {
    const rows = [...twoDays("a", 200, 60, 10, 120), ...twoDays("z", 200, 60, 10, 120)];
    const c = computeCallers(rows, new Map([["a", "Big"], ["z", "Tiny"]]), new Map());
    expect(c.every((x) => x.benchmark === "account")).toBe(true);
  });
});

describe("data gap alarm", () => {
  const hours = ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-06"].map((d) => ({ day: d, hour: 13, dials: 2500 }));
  const at1430ist = new Date("2026-10-07T09:00:00Z");

  it("alarms when the last hour is far below normal", () => {
    expect(dataGap([...hours, { day: "2026-10-07", hour: 13, dials: 300 }], at1430ist)).toMatchObject({ alarm: true, hour: 13, median: 2500 });
  });

  it("stays quiet on a normal hour and outside working hours", () => {
    expect(dataGap([...hours, { day: "2026-10-07", hour: 13, dials: 2400 }], at1430ist).alarm).toBe(false);
    expect(dataGap(hours, new Date("2026-10-07T20:00:00Z")).alarm).toBe(false);
  });
});

it("median", () => {
  expect(median([3, 1, 2])).toBe(2);
  expect(median([4, 1, 2, 3])).toBe(2.5);
  expect(median([])).toBe(0);
});

describe("team rule", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is the first group when that is a sales team", () => {
    expect(teamFromGroups(["Team Alpha", "Team Beta"])).toBe("Team Alpha");
  });

  it("skips calling-software groups, in any case, to the next group", () => {
    for (const phone of ["Acefone Users", "Mcube Users", "New Joinees - Mcube", "ACEFONE USERS", "mcube users", "new joinees - MCUBE"]) {
      expect(teamFromGroups([phone, "Team Alpha"])).toBe("Team Alpha");
    }
    expect(teamFromGroups(["Acefone Users", "Mcube Users", "Team Beta", "Team Alpha"])).toBe("Team Beta");
  });

  it("trims names and skips blank entries", () => {
    expect(teamFromGroups(["", "   ", null, undefined, "  Team Alpha  "])).toBe("Team Alpha");
    expect(teamFromGroups([" Mcube Users ", "\tTeam Beta\n"])).toBe("Team Beta");
  });

  it("is empty when no group qualifies or the list is missing", () => {
    for (const groups of [[], undefined, null, ["Acefone Users", "Mcube Users", "New Joinees - Mcube"], ["", "  "], "Team Alpha"]) {
      expect(teamFromGroups(groups)).toBe("");
    }
  });

  it("skips only the whole words acefone and mcube, not longer words that contain them", () => {
    expect(CALLING_SOFTWARE.test("Mcubed Sales")).toBe(false);
    expect(teamFromGroups(["Mcubed Sales", "Team Alpha"])).toBe("Mcubed Sales");
    expect(teamFromGroups(["Acefones", "Team Alpha"])).toBe("Acefones");
  });

  it("stores the sales team, not a phone group listed first, in the daily users sync", async () => {
    const db = fakeD1();
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify([
      { ID: "u1", FirstName: "Asha", LastName: "K", MemberOfGroups: ["Mcube Users", "Team Alpha"] },
      { ID: "u2", FirstName: "Ravi", LastName: "S", MemberOfGroups: ["Acefone Users"] },   // no sales team: left out
      { ID: "u3", FirstName: "Rinku", LastName: "Jhala", MemberOfGroups: ["Mcube Users", "Team Alpha"] },   // shared account
    ]))));
    const env = { DB: db, LEADSQUARED_HOST: "h", LEADSQUARED_ACCESS_KEY: "k", LEADSQUARED_SECRET_KEY: "s" } as unknown as Env;
    await runUsers(env);
    expect(db.sqlite.prepare("SELECT id, team FROM users ORDER BY id").all())
      .toEqual([{ id: "u1", team: "Team Alpha" }, { id: "u3", team: SHARED_TEAM }]);
  });

  it("clears the team of a user who comes back with no sales team", async () => {
    const db = fakeD1();
    const reply = (groups: string[]) => vi.fn(async () => new Response(JSON.stringify([
      { ID: "u1", FirstName: "Asha", LastName: "K", MemberOfGroups: groups },
      { ID: "u2", FirstName: "Ravi", LastName: "S", MemberOfGroups: ["Team Beta"] },
    ])));
    const env = { DB: db, LEADSQUARED_HOST: "h", LEADSQUARED_ACCESS_KEY: "k", LEADSQUARED_SECRET_KEY: "s" } as unknown as Env;
    vi.stubGlobal("fetch", reply(["Team Alpha", "Mcube Users"]));
    await runUsers(env);
    vi.stubGlobal("fetch", reply(["Mcube Users"]));                  // removed from Team Alpha, left on the phone system
    await runUsers(env);
    expect(db.sqlite.prepare("SELECT id, team FROM users ORDER BY id").all())
      .toEqual([{ id: "u1", team: "" }, { id: "u2", team: "Team Beta" }]);
  });
});
