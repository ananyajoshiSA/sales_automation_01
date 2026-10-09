import { afterEach, describe, expect, it, vi } from "vitest";
// @ts-ignore -- Node's fs: vitest runs on Node, and @types/node is not installed for the Worker build.
import { readFileSync } from "node:fs";
import worker from "../src/index";
import { CI_OFF, NO_SNAPSHOT, UPDATING, assemble } from "../src/ci";
import type { Env } from "../src/tasks";
import { fakeD1 } from "./fake_d1.mjs";

type TestEnv = Env & { DB: ReturnType<typeof fakeD1>; CI_ENABLED?: string };
const mkEnv = (over: Partial<TestEnv> = {}) => ({ DB: fakeD1(), LEADSQUARED_HOST: "h", LEADSQUARED_ACCESS_KEY: "k",
  LEADSQUARED_SECRET_KEY: "s", CI_ENABLED: "1", ACCESS_LOCAL_DEV: "1", ...over }) as TestEnv;
// localhost + ACCESS_LOCAL_DEV skips the Access check (as in `wrangler dev`); the real hostname never does.
const local = (env: Env, path: string, init?: RequestInit) => worker.fetch(new Request(`http://localhost:8787${path}`, init), env);
const remote = (env: Env, path: string, init?: RequestInit) =>
  worker.fetch(new Request(`https://sales-automation-01.example.workers.dev${path}`, init), env);
const GEN = "2026-10-09 06:30:00";

function loadParts(env: TestEnv, key: string, bodies: string[], { gen = GEN, parts = bodies.length, numbers }: { gen?: string | string[]; parts?: number; numbers?: number[] } = {}) {
  bodies.forEach((b, i) => env.DB.sqlite.prepare("INSERT INTO ci_snapshot (range_key, part, parts, generated_at, body) VALUES (?, ?, ?, ?, ?)")
    .run(key, numbers ? numbers[i] : i + 1, parts, Array.isArray(gen) ? gen[i] : gen, b));
}
const queries = (env: TestEnv, fn: () => Promise<Response>) => async () => {
  const n = env.DB.log.length;
  const r = await fn();
  return { r, n: env.DB.log.length - n };
};

afterEach(() => vi.restoreAllMocks());

describe("/api/ci routes", () => {
  it("answer 404 when switched off, without touching D1", async () => {
    for (const flag of [undefined, "0", "true"]) {
      const env = mkEnv({ CI_ENABLED: flag });
      for (const path of ["/api/ci/ranges", "/api/ci/snapshot?range=7d"]) {
        const { r, n } = await queries(env, () => local(env, path))();
        expect(r.status).toBe(404);
        expect(await r.json()).toEqual({ error: CI_OFF });
        expect(n).toBe(0);
      }
    }
  });

  it("sit behind the same Access check as /api/summary", async () => {
    const notSetUp = mkEnv();
    const setUp = mkEnv({ ACCESS_TEAM_DOMAIN: "acme.cloudflareaccess.com", ACCESS_AUD: "aud" });
    for (const env of [notSetUp, setUp, mkEnv({ CI_ENABLED: "0" })]) {
      const summary = await remote(env, "/api/summary");
      for (const path of ["/api/ci/ranges", "/api/ci/snapshot?range=7d", "/api/ci/snapshot?range=../x"]) {
        const r = await remote(env, path);
        expect(r.status).toBe(summary.status);
        expect(r.status).toBe(403);
        expect(await r.json()).toEqual(await summary.clone().json());
      }
    }
    const fake = await remote(setUp, "/api/ci/ranges", { headers: { "Cf-Access-Jwt-Assertion": "not.a.jwt" } });
    expect(await fake.json()).toMatchObject({ error: "Access login required (malformed token)" });
  });

  it("lists the loaded ranges, fixed ones first, newest day next", async () => {
    const env = mkEnv();
    const add = env.DB.sqlite.prepare("INSERT INTO ci_snapshot_index VALUES (?, ?, ?, ?, ?, ?, ?)");
    for (const [k, label] of [["2026-10-05", "5 Oct"], ["30d", "Last 30 days"], ["today", "Today"], ["2026-10-07", "7 Oct"], ["7d", "Last 7 days"]]) {
      add.run(k, label, "2026-10-01", "2026-10-08", GEN, 2, 1234);
    }
    const { r, n } = await queries(env, () => local(env, "/api/ci/ranges"))();
    expect(r.status).toBe(200);
    expect(n).toBe(1);
    expect(r.headers.get("cache-control")).toBe("private, max-age=60");
    const body = await r.json() as { ranges: { key: string }[] };
    expect(body.ranges.map((x) => x.key)).toEqual(["today", "7d", "30d", "2026-10-07", "2026-10-05"]);
    expect(body.ranges[1]).toEqual({ key: "7d", label: "Last 7 days", from: "2026-10-01", to: "2026-10-08", generatedAt: GEN, parts: 2, bytes: 1234 });
    expect(await (await local(mkEnv(), "/api/ci/ranges")).json()).toEqual({ ranges: [] });
  });

  it("joins a snapshot's parts in order with one D1 query", async () => {
    const env = mkEnv();
    const snap = JSON.stringify({ version: "ci-snapshot-1", range: { key: "7d" }, note: "Asha's ₹25,000 \"EMI\" \\ फीस" });
    loadParts(env, "7d", [snap.slice(0, 10), snap.slice(10, 31), snap.slice(31)]);
    loadParts(env, "today", ["{}"]);
    const { r, n } = await queries(env, () => local(env, "/api/ci/snapshot?range=7d"))();
    expect(r.status).toBe(200);
    expect(n).toBe(1);
    expect(r.headers.get("content-type")).toBe("application/json");
    expect(r.headers.get("cache-control")).toBe("private, max-age=60");
    expect(r.headers.get("x-ci-generated-at")).toBe(GEN);
    expect(await r.text()).toBe(snap);
  });

  it("returns a large body byte-identical, without parsing it", async () => {
    const env = mkEnv();
    // Not even valid JSON: the Worker must pass the stored text through untouched.
    const chunk = (i: number) => `{"part":${i},"x":"${"'\\\"₹फ ".repeat(4000)}"` + (i % 2 ? "]" : "");
    const bodies = Array.from({ length: 12 }, (_, i) => chunk(i));
    loadParts(env, "30d", bodies);
    const parse = vi.spyOn(JSON, "parse");
    const r = await local(env, "/api/ci/snapshot?range=30d");
    const text = await r.text();
    expect(parse).not.toHaveBeenCalled();
    expect(r.status).toBe(200);
    expect(text).toBe(bodies.join(""));
    expect(new TextEncoder().encode(text).length).toBeGreaterThan(300_000);
  });

  it("answers 503 while a load is incomplete or mixes two loads", async () => {
    const cases: [string[], Parameters<typeof loadParts>[3]][] = [
      [["{", "}"], { parts: 3 }],                                   // a part still missing
      [["{", "}"], { numbers: [1, 3], parts: 2 }],                  // a gap in the numbering
      [["{", "}"], { gen: [GEN, "2026-10-09 07:00:00"] }],          // parts of two loads
    ];
    for (const [bodies, opts] of cases) {
      const env = mkEnv();
      loadParts(env, "yesterday", bodies, opts);
      const r = await local(env, "/api/ci/snapshot?range=yesterday");
      expect(r.status).toBe(503);
      expect(r.headers.get("retry-after")).toBe("60");
      expect(r.headers.get("cache-control")).toBe("no-store");
      expect(await r.json()).toEqual({ error: UPDATING });
    }
    const mixed = mkEnv();                                          // parts disagree on how many there are
    loadParts(mixed, "7d", ["{"], { parts: 2 });
    mixed.DB.sqlite.prepare("INSERT INTO ci_snapshot VALUES ('7d', 2, 3, ?, '}')").run(GEN);
    expect((await local(mixed, "/api/ci/snapshot?range=7d")).status).toBe(503);
    expect(assemble([{ part: 1, parts: 2, generated_at: GEN, body: "{" }, { part: 2, parts: 3, generated_at: GEN, body: "}" }])).toBeNull();
    expect(assemble([])).toBeNull();
  });

  it("rejects a bad range with 400 and an unknown one with 404", async () => {
    const env = mkEnv();
    for (const q of ["", "?range=", "?range=week", "?range=2026-1-1", "?range=7d%27%3B--", "?range=../7d", "?range=TODAY"]) {
      const { r, n } = await queries(env, () => local(env, `/api/ci/snapshot${q}`))();
      expect(r.status).toBe(400);
      expect(n).toBe(0);
    }
    const r = await local(env, "/api/ci/snapshot?range=2026-10-01");
    expect(r.status).toBe(404);
    expect(await r.json()).toEqual({ error: NO_SNAPSHOT });
    expect((await local(env, "/api/ci/other")).status).toBe(404);
    expect((await local(env, "/api/ci/ranges", { method: "POST" })).status).toBe(405);
  });

  it("shows the empty state, not a database error, before migration 0004 is applied", async () => {
    const env = mkEnv();
    for (const t of ["ci_snapshot", "ci_snapshot_index"]) env.DB.sqlite.prepare(`DROP TABLE ${t}`).run();
    const ranges = await local(env, "/api/ci/ranges");
    expect(ranges.status).toBe(200);
    expect(await ranges.json()).toEqual({ ranges: [] });
    const snap = await local(env, "/api/ci/snapshot?range=7d");
    expect(snap.status).toBe(404);
    expect(await snap.json()).toEqual({ error: NO_SNAPSHOT });
    const busy = mkEnv();                                           // any other database error still surfaces
    const stmt = { bind: () => stmt, all: async () => { throw new Error("D1_ERROR: storage busy"); } };
    (busy.DB as unknown as { prepare: () => typeof stmt }).prepare = () => stmt;
    for (const path of ["/api/ci/ranges", "/api/ci/snapshot?range=7d"]) {
      const r = await local(busy, path);
      expect(r.status).toBe(500);
      expect(await r.json()).toEqual({ error: "D1_ERROR: storage busy" });
    }
  });

  it("never writes the ci tables", async () => {
    const env = mkEnv();
    loadParts(env, "7d", ["{}"]);
    await local(env, "/api/ci/ranges");
    await local(env, "/api/ci/snapshot?range=7d");
    expect(env.DB.log.every((sql) => /^\s*SELECT\b/i.test(sql))).toBe(true);
  });
});

// ---- the page renderer (public/ci.js) on the synthetic snapshot the Python tests also use ----------

type CiApi = { html: (s: unknown, st?: Record<string, string | undefined>, o?: Record<string, unknown>) => string;
  render: (s: unknown, root: Record<string, unknown>, o?: Record<string, unknown>) => void; VIEWS: string[] };
const here = (import.meta as unknown as { url: string }).url;
const read = (rel: string): string => readFileSync(new URL(rel, here), "utf8");
function loadCi(): CiApi {
  const win: { CI?: CiApi } = {};
  new Function("window", read("../public/ci.js"))(win);
  return win.CI!;
}
const fixture = () => JSON.parse(read("../../tests/fixtures/convintel_snapshot.json"));
const text = (h: string) => h.replace(/<[^>]+>/g, " ").replace(/&#39;/g, "'").replace(/&quot;/g, '"').replace(/&amp;/g, "&").replace(/\s+/g, " ");

describe("ci.js renderer", () => {
  const CI = loadCi();

  it("renders every view and drilldown of the fixture without gaps in the data", () => {
    const states = [...CI.VIEWS.map((v) => ({ v })), { v: "team", team: "Team Alpha +Ravi" },
      { v: "caller", team: "Team Alpha +Ravi", caller: "u-asha" }, { v: "lead", lead: "L-1001" }, { v: "call", call: "c-0001" },
      { v: "calls", fteam: "Team Beta", fcat: "emi_or_finance" }, { v: "leads", fcourse: "Diploma in Contract Drafting" }];
    for (const st of states) {
      const h = CI.html(fixture(), st);
      expect(h).toContain("Conversation Intelligence &amp; Team Analytics");
      for (const bad of ["undefined", "NaN", "[object Object]"]) expect(text(h)).not.toContain(bad);
    }
    for (const v of CI.VIEWS) expect(text(CI.html({}, { v }))).not.toMatch(/undefined|NaN|\[object Object\]/);
  });

  it("says what a real call means here, how fresh the data is, and how much is analysed", () => {
    const t = text(CI.html(fixture()));
    expect(t).toContain("a real call means answered and 3+ minutes");
    expect(t).toContain(`The main dashboard's "real conversation" (answered, 2+ minutes) is unchanged`);
    expect(t).toContain("Data as of 9 Oct 2026, 11:40 IST");
    expect(t).toContain("8 of 111 expected transcripts (7.2%)");
  });

  it("escapes every value taken from the snapshot", () => {
    const s = fixture();
    const evil = `<img src=x onerror="alert(1)">`;
    s.callers[0].caller = evil; s.calls[0].caller = evil; s.calls[0].findings[0].reasoning = evil;
    s.leads[0].nextAction = evil; s.teams[0].teamLeader = evil; s.filters.courses.push(evil);
    for (const st of [{ v: "callers" }, { v: "calls" }, { v: "call", call: "c-0001" }, { v: "leads" }, { v: "teams" }]) {
      const h = CI.html(s, st);
      expect(h).not.toContain("<img");
      expect(h).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
    }
  });

  it("drills down Organisation -> Team -> Caller -> Lead -> Call", () => {
    const s = fixture();
    expect(CI.html(s, { v: "overview", range: "7d" })).toContain('href="#range=7d&amp;v=team&amp;team=Team+Alpha+%2BRavi"');
    const team = CI.html(s, { v: "team", team: "Team Alpha +Ravi" });
    expect(team).toContain("v=caller&amp;team=Team+Alpha+%2BRavi&amp;caller=u-asha");
    const caller = CI.html(s, { v: "caller", team: "Team Alpha +Ravi", caller: "u-asha" });
    expect(caller).toContain("v=lead&amp;team=Team+Alpha+%2BRavi&amp;caller=u-asha&amp;lead=L-1001");
    const lead = CI.html(s, { v: "lead", team: "Team Alpha +Ravi", caller: "u-asha", lead: "L-1001" });
    expect(lead).toContain("call=c-0001");
    const call = text(CI.html(s, { v: "call", team: "Team Alpha +Ravi", caller: "u-asha", lead: "L-1001", call: "c-0001" }));
    expect(call).toContain("Organisation › Team Alpha +Ravi › Asha Verma › Lead L-1001 › Call 2026-10-08 16:20 IST");
    expect(call).toContain("Transcript intelligence");
    expect(call).toContain("Readiness to pay 78/100");
    expect(call).toContain("Next step: Send the payment link today");
  });

  it("shows excerpts only when the snapshot allows them", () => {
    const s = fixture();
    s.calls[0].findings[0].excerpt = "haan main Friday tak pay kar dunga";
    expect(CI.html(s, { v: "call", call: "c-0001" })).not.toContain("Friday tak");
    s.privacy.excerpts = true;
    expect(CI.html(s, { v: "call", call: "c-0001" })).toContain("haan main Friday tak pay kar dunga");
  });

  it("keeps shared logins out of the caller ranking", () => {
    const h = CI.html(fixture(), { v: "callers" });
    const [ranked, rest] = h.split("Shared logins and automation: in organisation totals, never ranked");
    const table = ranked.split("<tbody>")[1];
    expect(table).toContain("Asha Verma");
    expect(table).not.toContain("Admin");
    expect(rest).toContain("Admin");
  });

  it("filters lists client-side and says how many of how many are shown", () => {
    const s = fixture();
    const beta = text(CI.html(s, { v: "calls", fteam: "Team Beta" }));
    expect(beta).toContain("Showing 2 of 8 listed calls (340 in total; the list is capped)");
    expect(beta).not.toContain("Asha Verma Team Alpha");
    expect(text(CI.html(s, { v: "leads" }))).toContain("Showing 5 of 80 leads (the list is capped)");
    expect(text(CI.html(s, { v: "coverage" }))).toContain("Showing 4 of 103 calls (the list is capped)");
    expect(text(CI.html(s, { v: "opportunities" }))).toContain("Showing 4 of 9 opportunities");
    const led = text(CI.html(s, { v: "teams", fleader: "Ravi" })).split("Teams compared with the organisation")[1];
    expect(led).toContain("Team Alpha +Ravi Ravi (team name)");
    expect(led).not.toContain("Team Beta");
    expect(CI.html(s, { v: "calls", fstatus: "ANALYZED" })).toContain('<option value="ANALYZED" selected>');
  });

  it("gives a lead without a team its owner's team, in the list and under a team filter", () => {
    const s = fixture();
    expect(s.leads.find((l: { leadId: string }) => l.leadId === "L-1003").team).toBeNull();   // as LeadSquared sends it
    const beta = text(CI.html(s, { v: "leads", fteam: "Team Beta" }));
    expect(beta).toContain("L-1003 Chitra Nair Team Beta");
    expect(beta).not.toContain("L-1001");
    expect(text(CI.html(s, { v: "leads", fleader: "Ravi" }))).not.toContain("L-1003");
    expect(text(CI.html(s, { v: "lead", lead: "L-1003" }))).toContain("Owner's team Team Beta");
  });

  it("flags possible non-real conversations as needing review, per caller and per call", () => {
    const t = text(CI.html(fixture(), { v: "integrity" }));
    expect(t).toContain("A flag means the call needs a listen, not that it was faked");
    expect(t).toContain("Bilal Khan Team Alpha +Ravi Person 30 4 25 83.3% 2 40%");
    expect(t).toContain("showing 2 of 27");
    // The per-call list holds the calls the "Flagged calls" tile counts (short calls are counted on their own).
    expect(t).toContain("Flagged calls 6 real calls that may not be real conversations");
    expect(t).toContain("Showing 4 of 6 flagged calls (the list is capped)");
    const short = fixture();                                     // a short call the keyword layer also doubts
    short.calls[4].findings = [{ category: "possible_not_real", confidence: "medium", reasoning: "Only 4 words.", action: "Listen." }];
    const h = CI.html(short, { v: "integrity" });
    const list = h.split("<h2>Flagged calls</h2>")[1].split("</section>")[0];
    expect(list).toContain("call=c-0004");
    expect(list).not.toContain("call=c-0005");
    expect(text(h)).toContain("Showing 4 of 6 flagged calls");
    expect(t).toContain("Machine or IVR The transcript reads like a recorded message or IVR; needs a listen, not proof. Most of the transcript is a recorded message.");
    const s = fixture();
    delete s.calls[3].integrityReasons;            // an older snapshot: the flag's own meaning is shown
    expect(text(CI.html(s, { v: "integrity" }))).toContain("Machine or IVR recorded message, IVR or voicemail Most of the transcript");
  });

  it("shows revenue as not measurable yet, with the reason", () => {
    const t = text(CI.html(fixture(), { v: "revenue" }));
    expect(t).toContain("Revenue: not measurable yet. LeadSquared returned no payment records for this period");
    expect(t).toContain("First-time enrolments 4");
    expect(t).toContain("Enrolments not credited (lead owner at enrolment) 1");
    expect(t).toContain("enrolments lead owner at enrolment owner was a shared login, which is not one person 1");
    expect(t).not.toContain("Revenue (owner)");
  });

  it("shows revenue from revenue_section()'s total and amounts once payments carry an amount", () => {
    const s = fixture();
    const r = s.revenue;
    Object.assign(r, {
      measurable: true, total: 37500.5, amountField: "data.Amount", paymentEvents: 3,
      reason: "Amounts read from the payment field data.Amount (2 of 3 payment records have one).",
      amounts: {
        payments: 2, withoutAmount: 1,
        byTeam: { ownerAtEnrolment: { "Team Alpha +Ravi": 25000 }, lastAnsweredCaller: { "Team Beta": 25000 } },
        unattributed: { ownerAtEnrolment: 12500.5, lastAnsweredCaller: 12500.5 },
        unattributedReasons: { ownerAtEnrolment: { "no first-time enrolment for this lead in the period, so no owner at enrolment": 1 },
          lastAnsweredCaller: {} },
      },
      fields: { records: 3, note: "Names and types only.", amountCandidates: [{ field: "data.Amount", readable: 2 }],
        fields: [{ field: "data.Amount", section: "data", present: 3, filled: 2, types: { number: 2, empty: 1 } }] },
    });
    r.byCallerDetail.ownerAtEnrolment[0].revenue = 25000;
    const t = text(CI.html(s, { v: "revenue" }));
    expect(t).not.toContain("not measurable yet");
    expect(t).toContain("Revenue ₹37,501 from the field data.Amount");
    expect(t).toContain("Payment records 3 2 with an amount, 1 without");
    expect(t).toContain("Revenue not credited (lead owner at enrolment) ₹12,501");
    expect(t).toMatch(/Team Alpha \+Ravi 2 1 ₹25,000 –/);
    expect(t).toMatch(/Asha Verma Team Alpha \+Ravi 2 1 ₹25,000/);
    expect(t).toContain("payments lead owner at enrolment no first-time enrolment for this lead in the period");
    expect(t).toContain("data.Amount 3 2 number 2, empty 1");
    expect(t).toContain("Fields that look like an amount: data.Amount (2 readable).");
    expect(text(CI.html(s, { v: "overview" }))).toContain("Revenue ₹37,501 from payment records");
  });

  it("render() draws into a root and re-sorts on a header click", () => {
    const root: Record<string, any> = {};
    CI.render(fixture(), root, { state: { v: "callers" } });
    expect(root.innerHTML).toContain("Callers compared with their team");
    const th = { dataset: { t: "callers", i: "2" }, classList: { contains: (c: string) => c === "num" } };
    const click = () => root.onclick({ target: { closest: (sel: string) => (sel === "th[data-t]" ? th : null) } });
    const order = () => {
      const h = root.innerHTML.split("Shared logins")[0].split("<tbody>")[1];
      return ["Asha Verma", "Bilal Khan", "Chitra Nair"].map((n) => h.indexOf(n));
    };
    click();                            // numbers sort biggest first: dials 110, 100, 85
    let [a, b, c] = order();
    expect(a).toBeLessThan(b);
    expect(b).toBeLessThan(c);
    expect(root.innerHTML).toContain('aria-sort="descending">Dials');
    click();                            // a second click flips it
    [a, b, c] = order();
    expect(c).toBeLessThan(b);
    expect(b).toBeLessThan(a);
    CI.render(null, root, { message: "This view is being updated. Try again in a minute.", ranges: [{ key: "7d", label: "Last 7 days" }] });
    expect(root.innerHTML).toContain("being updated");
    expect(root.innerHTML).toContain('data-range="7d"');
  });
});
