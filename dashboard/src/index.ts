// Sales dashboard Worker (Cloudflare free tier).
//   cron (every minute) -> one small ingest task (see tasks.ts)
//   GET  /api/summary?from=YYYY-MM-DD&to=YYYY-MM-DD   dashboard data (IST days, at most 31), cached
//   GET  /api/health                                  sync and budget status, no names, no login
//   POST /api/run?task=calls_out|calls_in|leads|zip|enroll|users|arrivals   run one task now (Bearer RUN_TOKEN)
//   GET  /api/ci/ranges, /api/ci/snapshot?range=KEY   conversation intelligence snapshots (ci.ts)
// Everything else is served from ./public as static assets.
// DASHBOARD_OFF = "1" switches the whole dashboard off: the cron does nothing (no LeadSquared reads, no D1
// writes), every /api/* route answers 503 and every page shows a "switched off" notice. The D1 data is kept.
// The hostname sits behind Cloudflare Access; every /api/* route except /api/health also checks
// the Access token itself (access.ts) and refuses until Access is configured.
import { NOT_PROTECTED, verifyAccess } from "./access";
import { serveCi } from "./ci";
import { istDay } from "./lsq";
import { TASKS, health, json, localDev, parseRange, runAuthorized, serveSummary } from "./routes";
import { Env, Task, runTask, taskForMinute } from "./tasks";

export const OFF_MESSAGE = "The sales dashboard is switched off.";
const OFF_PAGE = `<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Sales dashboard</title></head>
<body style="font-family: system-ui, sans-serif; margin: 3rem 1rem; text-align: center">
<h1>${OFF_MESSAGE}</h1><p>It no longer reads LeadSquared or shows data.</p></body></html>`;

export const isOff = (env: Env): boolean => env.DASHBOARD_OFF === "1";

function switchedOff(url: URL): Response {
  if (url.pathname.startsWith("/api/")) return json({ error: OFF_MESSAGE, off: true }, 503);
  return new Response(OFF_PAGE, { status: 503, headers: { "content-type": "text/html; charset=utf-8", "cache-control": "no-store" } });
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (isOff(env)) return switchedOff(url);
    try {
      if (!url.pathname.startsWith("/api/")) {
        return env.ASSETS ? await env.ASSETS.fetch(req) : json({ error: "not found" }, 404);
      }
      if (url.pathname === "/api/health") return await health(env);
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
      if (url.pathname.startsWith("/api/ci/")) return await serveCi(url, req, env);
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
    if (isOff(env)) return;
    const task = taskForMinute(Math.floor(event.scheduledTime / 60_000));
    ctx.waitUntil(runTask(env, task).then(
      (r) => console.log(JSON.stringify(r)),
      (err) => console.error(`${task} failed: ${err instanceof Error ? err.message : err}`),
    ));
  },
} satisfies ExportedHandler<Env>;
