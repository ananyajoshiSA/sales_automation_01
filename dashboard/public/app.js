// Dashboard page: fetches /api/summary for the chosen IST date range and refreshes every minute
// while the tab is visible. Filters, search and sorting run on the loaded data (no extra requests).
// The page state lives in the URL, so a link opens the same view.
const REFRESH_MS = 60_000;
const MAX_DAYS = 31;
const BEHIND_ALERT_MIN = 45;
const DEFAULT_STATUS = "attention";
const state = { range: "today", from: "", to: "", team: "", status: DEFAULT_STATUS, q: "" };
const sorts = {};          // table id -> { i: column index, dir: 1 | -1 }
let data = null;
let lastLoad = 0;
let loadSeq = 0;

const IST_MS = 330 * 60_000;
const istToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(new Date());
const addDays = (day, n) => {
  const d = new Date(day + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
};
const spanDays = (a, b) => Math.round((Date.parse(b + "T00:00:00Z") - Date.parse(a + "T00:00:00Z")) / 86_400_000) + 1;
const DAY = /^\d{4}-\d{2}-\d{2}$/;
function rangeDates(r) {
  const t = istToday();
  if (r === "custom") return state.from && state.to ? [state.from, state.to] : null;
  return { today: [t, t], yesterday: [addDays(t, -1), addDays(t, -1)], "7d": [addDays(t, -6), t], "30d": [addDays(t, -29), t] }[r] ?? null;
}

/** "13:10 IST" for a UTC 'YYYY-MM-DD HH:MM:SS' string, with the IST date in front if it isn't today. */
function istTime(utc) {
  const ms = Date.parse(String(utc ?? "").slice(0, 19).replace(" ", "T") + "Z");
  if (!Number.isFinite(ms)) return "–";
  const ist = new Date(ms + IST_MS).toISOString();
  const day = ist.slice(0, 10);
  const clock = `${ist.slice(11, 16)} IST`;
  if (day === istToday()) return clock;
  const d = new Date(day + "T00:00:00Z").toLocaleDateString("en-IN", { day: "numeric", month: "short", timeZone: "UTC" });
  return `${d}, ${clock}`;
}

/** Sync errors start with their UTC time; show it in IST. */
const errorIst = (e) => String(e ?? "").replace(/^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) /, (_, t) => `${istTime(t)}: `);

const fmt = (n) => (n === null || n === undefined ? "–" : Number(n).toLocaleString("en-IN"));
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const $ = (id) => document.getElementById(id);
const cols = {};           // table id -> column specs of the last render (for sorting)
const rowsOf = {};         // table id -> unsorted rows of the last render

function sortValue(c, r) {
  const v = c.s ? c.s(r) : c.v(r);
  if (!c.num) return String(v ?? "").toLowerCase();
  const n = typeof v === "number" ? v : Number(String(v ?? "").replace(/,/g, ""));
  return Number.isFinite(n) ? n : -Infinity;
}

function table(id, spec, rows) {
  cols[id] = spec;
  rowsOf[id] = rows;
  const el = $(id);
  if (!rows.length) { el.innerHTML = `<tr><td class="empty">No data for this range yet.</td></tr>`; return; }
  const s = sorts[id];
  let list = rows;
  if (s && spec[s.i]) {
    const c = spec[s.i];
    list = rows.map((r, k) => [sortValue(c, r), k, r])
      .sort((a, b) => (a[0] < b[0] ? -s.dir : a[0] > b[0] ? s.dir : a[1] - b[1]))
      .map((x) => x[2]);
  }
  const head = spec.map((c, i) => {
    const arrow = s && s.i === i ? (s.dir === 1 ? " ▲" : " ▼") : "";
    const aria = s && s.i === i ? (s.dir === 1 ? "ascending" : "descending") : "none";
    return `<th class="sortable ${c.num ? "num" : ""}" data-i="${i}" aria-sort="${aria}" tabindex="0" title="Sort">${esc(c.h)}<span class="arrow">${arrow}</span></th>`;
  }).join("");
  el.innerHTML = `<thead><tr>${head}</tr></thead>` +
    `<tbody>${list.map((r) => `<tr>${spec.map((c) => `<td class="${c.cls || (c.num ? "num" : "")}">${c.html ? c.v(r) : esc(c.v(r))}</td>`).join("")}</tr>`).join("")}</tbody>`;
}

function onSort(e) {
  const th = e.target.closest("th.sortable");
  if (!th || (e.type === "keydown" && e.key !== "Enter" && e.key !== " ")) return;
  if (e.type === "keydown") e.preventDefault();
  const id = th.closest("table").id;
  const i = Number(th.dataset.i);
  const cur = sorts[id];
  // Numbers start high-to-low (biggest first), text A to Z; a second click flips it.
  const first = cols[id]?.[i]?.num ? -1 : 1;
  sorts[id] = { i, dir: cur && cur.i === i ? -cur.dir : first };
  table(id, cols[id], rowsOf[id]);
}

const pairs = (list) => list.map(([k, n]) => ({ k, n }));
const STATUS_ORDER = { lagging: 0, dialer_issue: 1, watch: 2, one_day: 3, ok: 4 };

function alertsHtml(list) {
  return list.map(([cls, msg]) => `<div class="alert ${cls}">${esc(msg)}</div>`).join("");
}

function renderFreshness(d) {
  const out = d.sync.find((s) => s.task === "calls_out");
  const behind = out && out.behindMin !== null ? out.behindMin : null;
  const at = d.paused ? `${d.paused.dataAsOfIst} IST` : istTime(d.generatedAt.replace(" UTC", ""));
  const lag = behind === null ? "" : ` · data about ${behind >= 120 ? `${Math.round(behind / 60)} h` : `${behind} min`} behind`;
  const el = $("fresh");
  el.textContent = `Updated ${at}${lag}`;
  el.classList.toggle("late", !!d.paused || (behind !== null && behind > BEHIND_ALERT_MIN));
}

function renderTeamOptions(callers) {
  const teams = [...new Set(callers.map((c) => c.team))].sort((a, b) => a.localeCompare(b));
  if (state.team && !teams.includes(state.team)) teams.unshift(state.team);
  const sel = $("team-filter");
  sel.innerHTML = `<option value="">All teams</option>` + teams.map((t) => `<option value="${esc(t)}">${esc(t)}</option>`).join("");
  sel.value = state.team;
}

function render() {
  if (!data) return;
  const d = data;
  $("meta").textContent = `${d.range.from === d.range.to ? d.range.from : `${d.range.from} to ${d.range.to}`} (IST days)`;
  renderFreshness(d);
  $("t-leads").textContent = fmt(d.leads.total);
  $("t-enroll").textContent = fmt(d.enrollments.total);
  $("t-dials").textContent = fmt(d.calls.dials);
  $("t-connect").textContent = d.calls.dials ? `${d.calls.connectPct}%` : "–";
  $("t-real").textContent = fmt(d.calls.realCalls);
  $("t-lagging").textContent = fmt(d.counts.lagging);
  $("t-dialer").textContent = fmt(d.counts.dialerIssue);

  const alerts = [];
  if (d.paused) alerts.push(["", `Numbers paused until ${d.paused.untilIst} IST: the free daily data limit was reached. Showing data as of ${d.paused.dataAsOfIst} IST.`]);
  if (d.dataGap.alarm) alerts.push(["bad", `Today: calls look missing. ${fmt(d.dataGap.dials)} dials in the ${d.dataGap.hour}:00 IST hour against a usual ${fmt(d.dataGap.median)}. Check the dialer to LeadSquared sync.`]);
  for (const s of d.sync) if (s.lastError) alerts.push(["bad", `Sync "${s.task}" error: ${errorIst(s.lastError)}`]);
  const behind = d.sync.filter((s) => !s.lastError && s.behindMin !== null && s.behindMin > BEHIND_ALERT_MIN);
  if (behind.length) {
    const max = Math.max(...behind.map((s) => s.behindMin));
    alerts.push(["", `Catching up with LeadSquared (${behind.map((s) => s.task).join(", ")}): up to ${max >= 120 ? `${Math.round(max / 60)} h` : `${max} min`} behind. Today's numbers are incomplete until this clears.`]);
  }
  if (d.budget.rowsWrittenToday > 0.8 * d.budget.limit) alerts.push(["", `Free-tier writes today: ${fmt(d.budget.rowsWrittenToday)} of ${fmt(d.budget.limit)}.`]);
  if (d.budget.readLimit && d.budget.rowsReadToday > 0.8 * d.budget.readLimit) alerts.push(["", `Free-tier reads today: ${fmt(d.budget.rowsReadToday)} of ${fmt(d.budget.readLimit)}. Numbers pause at the limit until 05:30 IST.`]);
  $("alerts").innerHTML = alertsHtml(alerts);

  renderTeamOptions(d.callers);
  const q = state.q.trim().toLowerCase();
  const callers = d.callers.filter((c) =>
    (state.status === "all" || (state.status === "watch" ? c.status === "watch" : ["lagging", "dialer_issue"].includes(c.status)))
    && (!state.team || c.team === state.team)
    && (!q || c.caller.toLowerCase().includes(q)));
  $("callers-count").textContent = `${callers.length} of ${d.callers.length} callers`;
  table("callers", [
    { h: "Status", html: true, s: (c) => STATUS_ORDER[c.status] ?? 9, num: false, v: (c) => `<span class="chip ${c.status}">${c.status.replace("_", " ")}</span>` },
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
    { h: "Last success (IST)", s: (s) => s.lastSuccess, v: (s) => istTime(s.lastSuccess) },
    { h: "Last error", cls: "flags", v: (s) => errorIst(s.lastError) },
  ], d.sync);
}

// ---- URL state ----------------------------------------------------------------------------

function writeUrl() {
  const p = new URLSearchParams();
  p.set("range", state.range);
  if (state.range === "custom") { p.set("from", state.from); p.set("to", state.to); }
  if (state.team) p.set("team", state.team);
  if (state.status !== DEFAULT_STATUS) p.set("status", state.status);
  if (state.q.trim()) p.set("q", state.q.trim());
  try { history.replaceState(null, "", `${location.pathname}?${p}`); } catch {}
  try { localStorage.setItem("range", state.range); } catch {}
}

function readUrl() {
  const p = new URLSearchParams(location.search);
  let saved = p.get("range");
  if (!saved) { try { saved = localStorage.getItem("range"); } catch {} }
  const from = p.get("from") ?? "", to = p.get("to") ?? "";
  if (saved === "custom" && validCustom(from, to) === "") Object.assign(state, { range: "custom", from, to });
  else if (saved && saved !== "custom" && rangeDates(saved)) state.range = saved;
  state.team = p.get("team") ?? "";
  const status = p.get("status");
  if (["attention", "watch", "all"].includes(status)) state.status = status;
  state.q = p.get("q") ?? "";
}

/** "" if the custom range is usable, else what's wrong, in words. */
function validCustom(from, to) {
  if (!DAY.test(from) || !DAY.test(to)) return "Pick both dates.";
  if (from > to) return "The start date is after the end date.";
  if (to > istToday()) return "The end date can't be after today.";
  if (spanDays(from, to) > MAX_DAYS) return `Pick at most ${MAX_DAYS} days.`;
  return "";
}

function syncControls() {
  for (const x of document.querySelectorAll("#ranges button")) x.classList.toggle("on", x.dataset.range === state.range);
  const [from, to] = rangeDates(state.range) ?? ["", ""];
  const today = istToday();
  $("from").max = today; $("to").max = today;
  $("from").value = from; $("to").value = to;
  $("status-filter").value = state.status;
  $("search").value = state.q;
}

// ---- loading --------------------------------------------------------------------------------

async function load() {
  const dates = rangeDates(state.range);
  if (!dates) return;
  const [from, to] = dates;
  const seq = ++loadSeq;
  lastLoad = Date.now();
  try {
    const r = await fetch(`/api/summary?from=${from}&to=${to}`, { cache: "no-store" });
    const body = await r.json().catch(() => ({}));
    if (seq !== loadSeq) return;           // a newer range was picked meanwhile
    if (!r.ok) {
      const err = new Error(body.error || `HTTP ${r.status}`);
      err.status = r.status;
      throw err;
    }
    data = body;
    render();
  } catch (e) {
    if (seq !== loadSeq) return;
    // A paused budget (503) or missing Access setup (403) is a state to explain, not a crash.
    const msg = e.status === 503 || e.status === 403 ? e.message : `Could not load data: ${e.message}`;
    $("alerts").innerHTML = alertsHtml([[e.status === 503 ? "" : "bad", msg]]);
  }
}

$("ranges").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  state.range = b.dataset.range;
  $("range-error").textContent = "";
  syncControls();
  writeUrl();
  load();
});

function onCustomDate() {
  const from = $("from").value, to = $("to").value;
  const err = validCustom(from, to);
  $("range-error").textContent = err;
  if (err) return;
  Object.assign(state, { range: "custom", from, to });
  syncControls();
  writeUrl();
  load();
}
$("from").addEventListener("change", onCustomDate);
$("to").addEventListener("change", onCustomDate);

$("status-filter").addEventListener("change", (e) => { state.status = e.target.value; writeUrl(); render(); });
$("team-filter").addEventListener("change", (e) => { state.team = e.target.value; writeUrl(); render(); });
$("search").addEventListener("input", (e) => { state.q = e.target.value; writeUrl(); render(); });
document.addEventListener("click", onSort);
document.addEventListener("keydown", onSort);

// No refreshes in a hidden tab; catch up as soon as it is looked at again.
setInterval(() => { if (!document.hidden) load(); }, REFRESH_MS);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && Date.now() - lastLoad > REFRESH_MS) load();
});

readUrl();
syncControls();
load();
