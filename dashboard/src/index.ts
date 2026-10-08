// Sales dashboard Worker (Cloudflare free tier).
//   cron (every minute) -> one small ingest task (see tasks.ts)
//   GET  /api/summary?from=YYYY-MM-DD&to=YYYY-MM-DD   dashboard data (IST days)
//   GET  /api/health                                  sync status, no LeadSquared calls
//   POST /api/run?task=calls_out|calls_in|leads|zip|enroll|users   run one task now
// Everything else is served from ./public as static assets (free, no Worker invocation).
// Put the whole hostname behind Cloudflare Access: the data includes lead and staff details.
import { istDay } from "./lsq";
import { summary } from "./metrics";
import { Env, Task, runTask, rowsWrittenToday, taskForMinute } from "./tasks";

const DAY = /^\d{4}-\d{2}-\d{2}$/;
const TASKS: Task[] = ["calls_out", "calls_in", "leads", "zip", "enroll", "users"];

const json = (data: unknown, status = 200) =>
  new Response(JSON.stringify(data), { status, headers: { "content-type": "application/json", "cache-control": "no-store" } });

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    try {
      if (url.pathname === "/api/summary") {
        const today = istDay(new Date());
        const from = url.searchParams.get("from") ?? today;
        const to = url.searchParams.get("to") ?? today;
        if (!DAY.test(from) || !DAY.test(to) || from > to) return json({ error: "from/to must be YYYY-MM-DD, from <= to" }, 400);
        return json(await summary(env.DB, from, to, Number(env.WRITE_BUDGET ?? 90_000)));
      }
      if (url.pathname === "/api/health") {
        const sync = await env.DB.prepare("SELECT task, cursor, updated_at, last_error FROM sync_state").all();
        return json({ ok: true, rowsWrittenToday: await rowsWrittenToday(env.DB), sync: sync.results });
      }
      if (url.pathname === "/api/run" && req.method === "POST") {
        const task = url.searchParams.get("task") as Task;
        if (!TASKS.includes(task)) return json({ error: `task must be one of ${TASKS.join(", ")}` }, 400);
        return json(await runTask(env, task));
      }
      return json({ error: "not found" }, 404);
    } catch (err) {
      return json({ error: err instanceof Error ? err.message : String(err) }, 500);
    }
  },

  async scheduled(event: ScheduledController, env: Env, ctx: ExecutionContext): Promise<void> {
    const task = taskForMinute(Math.floor(event.scheduledTime / 60_000));
    ctx.waitUntil(runTask(env, task).then(
      (r) => console.log(JSON.stringify(r)),
      (err) => console.error(`${task} failed: ${err instanceof Error ? err.message : err}`),
    ));
  },
} satisfies ExportedHandler<Env>;
