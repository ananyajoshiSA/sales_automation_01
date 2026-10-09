// Sales dashboard Worker (Cloudflare free tier).
//   cron (every minute) -> one small ingest task (see tasks.ts)
//   GET  /api/summary?from=YYYY-MM-DD&to=YYYY-MM-DD   dashboard data (IST days, at most 31), cached
//   GET  /api/health                                  sync and budget status, no names, no login
//   POST /api/run?task=calls_out|calls_in|leads|zip|enroll|users|arrivals   run one task now (Bearer RUN_TOKEN)
// Everything else is served from ./public as static assets (free, no Worker invocation).
// The hostname sits behind Cloudflare Access; every /api/* route except /api/health also checks
// the Access token itself (access.ts) and refuses until Access is configured.
import { NOT_PROTECTED, verifyAccess } from "./access";
import { istDay } from "./lsq";
import { TASKS, health, json, localDev, parseRange, runAuthorized, serveSummary } from "./routes";
import { Env, Task, runTask, taskForMinute } from "./tasks";

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    try {
      if (url.pathname === "/api/health") return await health(env);
      if (!url.pathname.startsWith("/api/")) return json({ error: "not found" }, 404);
      const run = url.pathname === "/api/run" && req.method === "POST";
      if (run && !runAuthorized(req.headers.get("Authorization"), env.RUN_TOKEN)) {
        return json({ error: "POST /api/run needs Authorization: Bearer <RUN_TOKEN>" }, 401);
      }
      const access = localDev(url, env) ? { ok: true as const } : await verifyAccess(req, env);
      if (!access.ok) return json({ error: access.error, notProtected: access.error === NOT_PROTECTED }, access.status);

      if (url.pathname === "/api/summary") {
        const r = parseRange(url.searchParams, istDay(new Date()));
        if ("error" in r) return json({ error: r.error }, 400);
        return await serveSummary(env, r.from, r.to);
      }
      if (run) {
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
