// Dashboard page: fetches /api/summary for the chosen IST date range and refreshes every minute.
const REFRESH_MS = 60_000;
let range = "today";
let data = null;

const istToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
const addDays = (day, n) => {
  const d = new Date(day + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
function rangeDates(r) {
  const t = istToday();
  return { today: [t, t], yesterday: [addDays(t, -1), addDays(t, -1)], "7d": [addDays(t, -6), t], "30d": [addDays(t, -29), t] }[r];
}

const fmt = (n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString("en-IN"));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (id) => document.getElementById(id);

function table(id, cols, rows) {
  const el = $(id);
  if (!rows.length) { el.innerHTML = `<tr><td class="empty">No data for this range yet.</td></tr>`; return; }
  el.innerHTML = `<thead><tr>${cols.map((c) => `<th class="${c.num ? "num" : ""}">${esc(c.h)}</th>`).join("")}</tr></thead>` +
    `<tbody>${rows.map((r) => `<tr>${cols.map((c) => `<td class="${c.cls || (c.num ? "num" : "")}">${c.html ? c.v(r) : esc(c.v(r))}</td>`).join("")}</tr>`).join("")}</tbody>`;
}

const pairs = (list) => list.map(([k, n]) => ({ k, n }));

function render() {
  if (!data) return;
  const d = data;
  $("meta").textContent = `${d.range.from === d.range.to ? d.range.from : `${d.range.from} → ${d.range.to}`} (IST) · updated ${new Date().toLocaleTimeString()}`;
  $("t-leads").textContent = fmt(d.leads.total);
  $("t-enroll").textContent = fmt(d.enrollments.total);
  $("t-dials").textContent = fmt(d.calls.dials);
  $("t-connect").textContent = d.calls.dials ? `${d.calls.connectPct}%` : "–";
  $("t-real").textContent = fmt(d.calls.realCalls);
  $("t-lagging").textContent = fmt(d.counts.lagging);
  $("t-dialer").textContent = fmt(d.counts.dialerIssue);

  const alerts = [];
  if (d.dataGap.alarm) alerts.push(["bad", `Today: calls look missing - ${fmt(d.dataGap.dials)} dials in the ${d.dataGap.hour}:00 hour vs a usual ${fmt(d.dataGap.median)}. Check the dialer → LeadSquared sync.`]);
  for (const s of d.sync) if (s.lastError) alerts.push(["bad", `Sync "${s.task}" error: ${s.lastError}`]);
  const behind = d.sync.filter((s) => !s.lastError && s.behindMin !== null && s.behindMin > 45);
  if (behind.length) {
    const max = Math.max(...behind.map((s) => s.behindMin));
    alerts.push(["", `Catching up with LeadSquared (${behind.map((s) => s.task).join(", ")}): up to ${max >= 120 ? `${Math.round(max / 60)} h` : `${max} min`} behind. Today's numbers are incomplete until this clears.`]);
  }
  if (d.budget.rowsWrittenToday > 0.8 * d.budget.limit) alerts.push(["", `Free-tier writes today: ${fmt(d.budget.rowsWrittenToday)} of ${fmt(d.budget.limit)}.`]);
  $("alerts").innerHTML = alerts.map(([cls, msg]) => `<div class="alert ${cls}">${esc(msg)}</div>`).join("");

  const filter = $("status-filter").value;
  const callers = d.callers.filter((c) => filter === "all" || (filter === "watch" ? c.status === "watch" : ["lagging", "dialer_issue"].includes(c.status)));
  table("callers", [
    { h: "Status", html: true, v: (c) => `<span class="chip ${c.status}">${c.status.replace("_", " ")}</span>` },
    { h: "Caller", v: (c) => c.caller }, { h: "Team", v: (c) => c.team },
    { h: "Days", num: true, v: (c) => c.activeDays }, { h: "Dials/day", num: true, v: (c) => c.dialsPerDay },
    { h: "Connect %", num: true, v: (c) => c.connectPct }, { h: "Real/day", num: true, v: (c) => c.realPerDay },
    { h: "Talk min/day", num: true, v: (c) => c.talkMinPerDay }, { h: "Fail %", num: true, v: (c) => c.failurePct },
    { h: "Enrolled", num: true, v: (c) => c.conversions }, { h: "Flags", cls: "flags", v: (c) => c.flags.join("; ") },
  ], callers);
  table("teams", [
    { h: "Team", v: (t) => t.team }, { h: "Callers", num: true, v: (t) => t.callers }, { h: "Lagging", num: true, v: (t) => t.lagging },
    { h: "Dials/day", num: true, v: (t) => t.medDialsPerDay }, { h: "Real/day", num: true, v: (t) => t.medRealPerDay },
    { h: "Connect %", num: true, v: (t) => t.medConnectPct }, { h: "Enrolled", num: true, v: (t) => t.conversions },
  ], d.teams);
  const z = d.zip;
  table("zip", [
    { h: "Group", v: (r) => r.g }, { h: "Calls analysed", num: true, v: (r) => fmt(r.analysed) },
    { h: "Pitch %", num: true, v: (r) => r.pitchPct ?? "–" }, { h: "Probing %", num: true, v: (r) => r.probePct ?? "–" },
    { h: "Objections %", num: true, v: (r) => r.objectionPct ?? "–" }, { h: "High/mod intent %", num: true, v: (r) => r.highOrModerateIntentPct },
  ], [{ g: "Lagging callers", ...z.lagging }, { g: "Everyone else", ...z.others }].filter((r) => r.analysed));
  table("lead-sources", [{ h: "Source", v: (r) => r.source }, { h: "Leads", num: true, v: (r) => fmt(r.n) }], d.leads.bySource);
  table("lead-teams", [{ h: "Owner's team", v: (r) => r.k }, { h: "Leads", num: true, v: (r) => fmt(r.n) }], pairs(d.leads.byTeam));
  table("enroll-teams", [{ h: "Team", v: (r) => r.k }, { h: "Enrolled", num: true, v: (r) => r.n }], pairs(d.enrollments.byTeam));
  table("enroll-owners", [{ h: "Lead owner", v: (r) => r.k }, { h: "Enrolled", num: true, v: (r) => r.n }], pairs(d.enrollments.byOwner));
  table("sync", [
    { h: "Task", v: (s) => s.task }, { h: "Behind (min)", num: true, v: (s) => s.behindMin ?? "–" },
    { h: "Last success (UTC)", v: (s) => s.lastSuccess }, { h: "Last error", cls: "flags", v: (s) => s.lastError ?? "" },
  ], d.sync);
}

async function load() {
  const [from, to] = rangeDates(range);
  try {
    const r = await fetch(`/api/summary?from=${from}&to=${to}`, { cache: "no-store" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || `HTTP ${r.status}`);
    data = await r.json();
    render();
  } catch (e) {
    $("alerts").innerHTML = `<div class="alert bad">Could not load data: ${esc(e.message)}</div>`;
  }
}

$("ranges").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  range = b.dataset.range;
  for (const x of document.querySelectorAll("#ranges button")) x.classList.toggle("on", x === b);
  try { localStorage.setItem("range", range); } catch {}
  load();
});
$("status-filter").addEventListener("change", render);
try {
  const saved = new URLSearchParams(location.search).get("range") || localStorage.getItem("range");
  if (saved && rangeDates(saved)) {
    range = saved;
    for (const x of document.querySelectorAll("#ranges button")) x.classList.toggle("on", x.dataset.range === saved);
  }
} catch {}
load();
setInterval(load, REFRESH_MS);
