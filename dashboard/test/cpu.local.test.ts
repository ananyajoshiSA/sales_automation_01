// Local-only CPU benchmark against real API responses (fixtures contain lead data, so they live
// outside the repo). Run: CPU_FIXTURES=/path/to/fixtures npx vitest run test/cpu.local.test.ts
import { readFileSync } from "node:fs";
import { it } from "vitest";
import { aggregateCalls, aggregateLeads, aggregateZip } from "../src/aggregate";
import { Call, parseCall } from "../src/lsq";
import { addOnConflict, int, multiInsert, str } from "../src/sql";

const dir = process.env.CPU_FIXTURES;
const load = (f: string) => readFileSync(`${dir}/${f}`, "utf8");

function time(label: string, fn: () => void) {
  const t0 = performance.now(); fn(); const cold = performance.now() - t0;
  const runs: number[] = [];
  for (let i = 0; i < 30; i++) { const t = performance.now(); fn(); runs.push(performance.now() - t); }
  runs.sort((a, b) => a - b);
  console.log(`${label.padEnd(44)} cold ${cold.toFixed(2).padStart(6)} ms   warm median ${runs[15].toFixed(2).padStart(5)} ms`);
}

it.skipIf(!dir)("CPU per cron run", () => {
  const calls = [load("calls_p1.json"), load("calls_p2.json")];
  const zips = [load("zip_p1.json"), load("zip_p2.json")];
  const leads = load("leads_bulk.json");
  const users = load("users.json");

  time("calls_out: 2 pages x 100 calls", () => {
    const acts = calls.flatMap((t) => JSON.parse(t).List);
    const b = aggregateCalls(acts.map(parseCall).filter((c: Call | null): c is Call => c !== null));
    multiInsert("caller_day", ["day", "user_id", "name", "dials"], b.callers.map((c) => [str(c.day), str(c.userId), str(c.name), int(c.dials)]),
      addOnConflict(["day", "user_id"], ["dials"]));
    multiInsert("lead_last_call", ["lead_id", "user_id", "at"], b.lastCall.map((l) => [str(l.leadId), str(l.userId), str(l.at.toISOString())]));
  });
  time("zip: 2 pages x 40 notes", () => {
    const acts = zips.flatMap((t) => JSON.parse(t).List);
    aggregateZip(acts, new Map());
  });
  time("leads: 1 page x 500 (bulk-import day)", () => { aggregateLeads(JSON.parse(leads)); });
  time("users: daily, 1,933 users", () => {
    const u = JSON.parse(users).map((x: any) => ({ id: x.ID, name: `${x.FirstName} ${x.LastName}`, team: (x.MemberOfGroups ?? [])[0] ?? "" }))
      .filter((x: any) => x.team);
    multiInsert("users", ["id", "name", "team"], u.map((x: any) => [str(x.id), str(x.name), str(x.team)]));
  });
});
