import { afterEach, describe, expect, it, vi } from "vitest";
import { SHARED_TEAM, resolveArrival, sharedNames } from "../src/accountability";
import { summary } from "../src/metrics";
import { runArrivals, taskForMinute } from "../src/tasks";
import type { Env } from "../src/tasks";
import { fakeD1 } from "./fake_d1.mjs";

const shared = sharedNames(undefined);
const moved = (t: string, prev: string, to: string, by: string) => ({ CreatedOn: t,
  Data: [{ Key: "PreviousOwner", Value: prev }, { Key: "CurrentOwner", Value: to }, { Key: "CreatedBy", Value: by }] });
const lead = (over: Record<string, string> = {}) => ({ ProspectID: "L1", CreatedOn: "2026-01-01 00:00:00",
  CreatedByName: "Pratik Sarkar", ...over });

describe("who put a lead into a shared account", () => {
  it("credits a personal login, never the account, and marks automation", () => {
    expect(resolveArrival(lead(), [moved("2026-10-07 05:00:00", "Ravi", "Rinku Jhala", "Pratik Sarkar")], "Rinku Jhala", shared))
      .toMatchObject({ how: "owner change", putBy: "Pratik Sarkar", status: "Verified", login: "Pratik Sarkar" });
    expect(resolveArrival(lead(), [moved("2026-10-07 05:00:00", "Ravi", "Rinku Jhala", "System")], "Rinku Jhala", shared))
      .toMatchObject({ putBy: "System (automation)", status: "Automated" });
  });

  it("uses Assigned By for a shared login only when it belongs to that change", () => {
    const change = [moved("2026-10-07 05:00:00", "Ravi", "Rinku Jhala", "Rinku Jhala")];
    const fresh = lead({ mx_Assigned_By: "Asha", mx_Assigned_On: "2026-10-07 05:01:00" });
    expect(resolveArrival(fresh, change, "Rinku Jhala", shared)).toMatchObject({ putBy: "Asha", status: "Verified (Assigned By)" });
    // Asha assigned this lead before, so the field probably predates the shared-login change.
    const stale = [moved("2026-09-01 05:00:00", "", "Ravi", "Asha"), ...change];
    expect(resolveArrival(fresh, stale, "Rinku Jhala", shared)).toMatchObject({ putBy: "Unverified", status: "Unverified", possible: "Asha" });
    expect(resolveArrival(lead(), change, "Rinku Jhala", shared)).toMatchObject({ putBy: "Unverified", possible: "" });
    expect(resolveArrival(lead({ mx_Assigned_By: "Asha", mx_Assigned_On: "2026-10-01 00:00:00" }), change, "Rinku Jhala", shared))
      .toMatchObject({ status: "Unverified", possible: "Asha" });
  });

  it("falls back to who created the lead there", () => {
    expect(resolveArrival(lead({ CreatedByName: "Rinku Jhala" }), [], "Rinku Jhala", shared))
      .toMatchObject({ how: "created there", putBy: "Unverified", status: "Unverified" });
    expect(sharedNames(" A , B,")).toEqual(new Set(["A", "B"]));
  });
});

describe("live sync and summary", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("checks arrivals twice an hour without disturbing the rest of the rotation", () => {
    expect(taskForMinute(15)).toBe("arrivals");
    expect(taskForMinute(45)).toBe("arrivals");
    expect(taskForMinute(17)).toBe("calls_in");
  });

  it("records who put new leads into the shared account, then keeps the first answer when it changes", async () => {
    const db = fakeD1();
    db.sqlite.prepare("INSERT INTO users VALUES ('rj', 'Rinku Jhala', ?, 'x'), ('u1', 'Asha', 'Team X', 'x')").run(SHARED_TEAM);
    const since = new Date(Date.now() - 30 * 60_000).toISOString().slice(0, 19).replace("T", " ");
    db.sqlite.prepare("INSERT INTO sync_state VALUES ('arrivals', ?, 1, 'x', NULL)").run(since);
    const t = (minAgo: number) => new Date(Date.now() - minAgo * 60_000).toISOString().slice(0, 19).replace("T", " ");
    let field = "";
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      const u = new URL(url);
      const body = u.pathname.endsWith("Leads.Get")
        ? [{ ProspectID: "L1", CreatedOn: t(500), CreatedByName: "Admin", mx_Assigned_By: field, mx_Assigned_On: t(9) },
           { ProspectID: "L0", CreatedOn: t(500), mx_Assigned_On: t(90) }]   // older than the cursor: skipped
        : { ProspectActivities: [moved(t(10), "Ravi", "Rinku Jhala", "Rinku Jhala")] };
      expect(u.searchParams.get("leadId") ?? "L1").toBe("L1");
      return new Response(JSON.stringify(body));
    }));
    const env = { DB: db, LEADSQUARED_HOST: "h", LEADSQUARED_ACCESS_KEY: "k", LEADSQUARED_SECRET_KEY: "s" } as unknown as Env;
    await runArrivals(env);
    const row = () => db.sqlite.prepare("SELECT * FROM account_arrival").all();
    expect(row()).toMatchObject([{ lead_id: "L1", account_id: "rj", put_by: "Unverified", status: "Unverified", login: "Rinku Jhala" }]);

    // Assigned By is filled in for the same change: re-attributed, the first answer kept.
    field = "Asha";
    db.sqlite.prepare("UPDATE sync_state SET cursor = ? WHERE task = 'arrivals'").run(since);
    await runArrivals(env);
    expect(row()).toMatchObject([{ put_by: "Asha", status: "Verified (Assigned By)", first_put_by: "Unverified", first_status: "Unverified" }]);

    // The summary credits Asha, keeps the admin out of the caller list, and labels her account as shared.
    const day = row()[0].day as string;
    db.sqlite.prepare("INSERT INTO caller_day (day, user_id, name, dials, inbound, inbound_missed) VALUES (?, 'rj', 'Rinku Jhala', 40, 6, 5), (?, 'u1', 'Asha', 40, 0, 0)").run(day, day);
    db.sqlite.prepare("INSERT INTO enrollment VALUES ('L9', ?, 'x', 'rj', 'System')").run(day);
    const { data } = await summary(db as unknown as D1Database, day, day, 90_000);
    expect(data.callers.map((c) => c.caller)).toEqual(["Asha"]);
    expect(data.enrollments.byOwner).toEqual([["Rinku Jhala (shared account)", 1]]);
    expect(data.accountability).toMatchObject({ arrivals: 1, unverified: 0, byPerson: [["Asha", 1]],
      sharedCalls: [{ account: "Rinku Jhala (shared account)", dials: 40, inbound: 6, inboundMissed: 5 }] });
  });
});
