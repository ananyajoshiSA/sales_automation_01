import { describe, expect, it } from "vitest";
import worker from "../src/index";
import { MAX_RANGE_DAYS, parseRange, runAuthorized, serveSummary } from "../src/routes";
import { countReads, recordError, usersDue, utcDay } from "../src/tasks";
import type { Env } from "../src/tasks";
import { fakeD1 } from "./fake_d1.mjs";

const mkEnv = (over: Partial<Env> = {}) => ({ DB: fakeD1(), LEADSQUARED_HOST: "h", LEADSQUARED_ACCESS_KEY: "k",
  LEADSQUARED_SECRET_KEY: "s", ...over }) as Env & { DB: ReturnType<typeof fakeD1> };
const call = (env: Env, path: string, init?: RequestInit) =>
  worker.fetch(new Request(`https://sales-automation-01.example.workers.dev${path}`, init), env);
const rowsIn = (env: { DB: ReturnType<typeof fakeD1> }, table: string) =>
  (env.DB.sqlite.prepare(`SELECT rows FROM ${table} WHERE day = ?`).all(utcDay())[0]?.rows as number) ?? 0;

describe("route rules", () => {
  it("validates the date range and caps it at 31 days", () => {
    const p = (q: string) => parseRange(new URLSearchParams(q), "2026-10-09");
    expect(p("")).toEqual({ from: "2026-10-09", to: "2026-10-09" });
    expect(p("from=2026-09-09&to=2026-10-09")).toEqual({ from: "2026-09-09", to: "2026-10-09" });
    expect(p("from=2026-09-08&to=2026-10-09")).toEqual({ error: `Pick at most ${MAX_RANGE_DAYS} days` });
    expect(p("from=2026-10-09&to=2026-10-01")).toHaveProperty("error");
    expect(p("from=9/10/2026")).toHaveProperty("error");
  });

  it("checks the run token exactly", () => {
    expect(runAuthorized("Bearer s3cret", "s3cret")).toBe(true);
    expect(runAuthorized("Bearer s3cre", "s3cret")).toBe(false);
    expect(runAuthorized("Bearer s3cretX", "s3cret")).toBe(false);
    expect(runAuthorized(null, "s3cret")).toBe(false);
    expect(runAuthorized("Bearer ", "")).toBe(false);
    expect(runAuthorized("Bearer undefined", undefined)).toBe(false);
  });

  it("refuses /api/run without or with a wrong bearer", async () => {
    const env = mkEnv({ RUN_TOKEN: "s3cret" });
    expect((await call(env, "/api/run?task=zip", { method: "POST" })).status).toBe(401);
    expect((await call(env, "/api/run?task=zip", { method: "POST", headers: { Authorization: "Bearer nope" } })).status).toBe(401);
    // Right token, but Access isn't set up yet: still refused, without touching LeadSquared.
    expect((await call(env, "/api/run?task=zip", { method: "POST", headers: { Authorization: "Bearer s3cret" } })).status).toBe(403);
  });

  it("refuses data until Access is configured, but health stays up with no names or error text", async () => {
    const env = mkEnv();
    env.DB.sqlite.prepare("INSERT INTO sync_state VALUES ('calls_out', '2026-10-09 07:00:00', 1, '2026-10-09 07:10:00', '2026-10-09 07:10:00 GET ProspectActivity.svc/Retrieve -> HTTP 401: Asha')").run();
    const r = await call(env, "/api/summary");
    expect(r.status).toBe(403);
    expect(await r.json()).toMatchObject({ error: "Dashboard not yet protected: finish Cloudflare Access setup", notProtected: true });
    const h = await call(env, "/api/health");
    expect(h.status).toBe(200);
    const text = await h.text();
    expect(text).not.toContain("Asha");
    expect(JSON.parse(text)).toMatchObject({ ok: true, accessConfigured: false, sync: [{ task: "calls_out", hasError: true, errorHttp: 401 }] });
  });

  it("rejects a range over 31 days with 400 (localhost dev bypass)", async () => {
    const env = mkEnv({ ACCESS_LOCAL_DEV: "1" });
    const r = await worker.fetch(new Request("http://localhost:8787/api/summary?from=2026-09-01&to=2026-10-09"), env);
    expect(r.status).toBe(400);
    // The bypass never applies to a real hostname.
    expect((await call(env, "/api/summary")).status).toBe(403);
  });
});

describe("summary cache and budgets", () => {
  const seed = (env: { DB: ReturnType<typeof fakeD1> }) => {
    env.DB.sqlite.prepare("INSERT INTO caller_day (day, user_id, name, dials, answered) VALUES ('2026-10-08', 'u1', 'Asha', 50, 20)").run();
    env.DB.sqlite.prepare("INSERT INTO users VALUES ('u1', 'Asha', 'Team X', '2026-10-08 00:00:00')").run();
  };

  it("computes once, then serves the stored text, and charges reads and writes", async () => {
    const env = mkEnv();
    seed(env);
    const miss = await serveSummary(env, "2026-10-08", "2026-10-08");
    expect(miss.headers.get("x-cache")).toBe("miss");
    const body = await miss.text();
    expect(JSON.parse(body)).toMatchObject({ calls: { dials: 50 }, range: { from: "2026-10-08", to: "2026-10-08" } });

    // Reads: the 3 pre-check rows found nothing + summary queries; writes: cache + read budget rows + itself.
    expect(rowsIn(env, "read_budget")).toBeGreaterThan(0);
    expect(rowsIn(env, "write_budget")).toBe(3);

    const n = env.DB.log.length;
    const hit = await serveSummary(env, "2026-10-08", "2026-10-08");
    expect(hit.headers.get("x-cache")).toBe("hit");
    expect(await hit.text()).toBe(body);
    expect(env.DB.log.length - n).toBe(3);   // one read batch, nothing else
    expect(rowsIn(env, "write_budget")).toBe(3);
  });

  it("pauses on the stale copy over the read budget, and 503s with nothing cached", async () => {
    const env = mkEnv({ READ_BUDGET: "10" });
    seed(env);
    env.DB.sqlite.prepare("INSERT INTO read_budget VALUES (?, 50)").run(utcDay());
    const none = await serveSummary(env, "2026-10-08", "2026-10-08");
    expect(none.status).toBe(503);
    expect(await none.json()).toMatchObject({ error: expect.stringContaining("paused until 05:30 IST"), paused: { untilIst: "05:30" } });

    env.DB.sqlite.prepare("INSERT INTO summary_cache VALUES ('2026-10-08|2026-10-08', '2026-10-09 07:40:00', 9, ?)")
      .run('{"range":{"from":"2026-10-08","to":"2026-10-08"}}');
    const stale = await serveSummary(env, "2026-10-08", "2026-10-08");
    expect(stale.status).toBe(200);
    expect(stale.headers.get("x-cache")).toBe("stale");
    expect(await stale.json()).toMatchObject({ paused: { untilIst: "05:30", dataAsOfIst: "13:10" }, range: { from: "2026-10-08" } });
  });
});

it("countReads charges every statement's rows read, first() included", async () => {
  const db = fakeD1();
  db.sqlite.prepare("INSERT INTO users VALUES ('a','A','T','x'), ('b','B','T','x')").run();
  const tally = { rowsRead: 0 };
  const c = countReads(db, tally);
  expect(await c.prepare("SELECT * FROM users WHERE id = ?").bind("a").first<{ name: string }>()).toMatchObject({ name: "A" });
  expect(await c.prepare("SELECT name FROM users WHERE id = ?").bind("b").first("name")).toBe("B");
  await c.prepare("SELECT * FROM users").all();
  await c.batch([c.prepare("SELECT * FROM users"), c.prepare("SELECT * FROM users WHERE id = ?").bind("zz")]);
  expect(tally.rowsRead).toBe(1 + 1 + 2 + 2);
});

describe("users refresh", () => {
  it("retries on the next slot after a failed run instead of waiting 20 hours", async () => {
    const env = mkEnv();
    expect(await usersDue(env.DB)).toBe(true);  // never run
    await recordError(env.DB, "users", new Error("HTTP 401"));
    expect(await usersDue(env.DB)).toBe(true);  // failed: try again
    env.DB.sqlite.prepare("UPDATE sync_state SET cursor = ?, last_error = NULL WHERE task = 'users'")
      .run(new Date().toISOString().slice(0, 19).replace("T", " "));
    expect(await usersDue(env.DB)).toBe(false); // fresh success
  });
});
