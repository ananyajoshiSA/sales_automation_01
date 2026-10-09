// Conversation Intelligence & Team Analytics page (ci.html). Shows one snapshot computed offline by
// analytics/convintel and served as stored text by /api/ci/snapshot. Filters, drilldowns (Organisation ->
// Team -> Caller -> Lead -> Call) and sorting all run on the loaded snapshot, with no extra requests; the
// view lives in the URL hash, so a link opens the same view.
// The same file renders a snapshot embedded in the page as <script type="application/json" id="ci-data">
// (analytics/convintel/snapshot_html.py): then nothing is fetched and the page works offline.
// Every value from the snapshot goes through esc() before it reaches innerHTML.
(function () {
  "use strict";
  const IST_MS = 330 * 60_000;
  const REFRESH_MS = 5 * 60_000;   // re-check the range list; the snapshot is fetched only when it changed
  const RETRY_MS = 60_000;         // after "being updated"
  const LATE_MIN = 180;            // a snapshot older than this is marked late
  const ROW_IDS = 5;               // call links shown per caller in the integrity table

  const VIEWS = [
    ["overview", "Organisation"], ["coverage", "Coverage"], ["teams", "Teams"], ["callers", "Callers"],
    ["leads", "Leads"], ["calls", "Calls"], ["integrity", "Possibly not real"], ["opportunities", "Opportunities"],
    ["zip", "Zipteams comparison"], ["coaching", "Coaching"], ["accountability", "Accountability"], ["revenue", "Revenue"],
  ];
  const DRILL_TAB = { team: "teams", caller: "callers", lead: "leads", call: "calls" };
  const FILTERS = [
    ["fteam", "teams", "All teams"], ["fleader", "teamLeaders", "All team leaders"], ["fcaller", "callers", "All callers"],
    ["fcourse", "courses", "All courses"], ["fstage", "stages", "All lead stages"],
    ["fcat", "categories", "All conversation categories"], ["fstatus", "statuses", "All analysis statuses"],
    ["fprio", "priorities", "All lead priorities"],
  ];
  const DRILL = ["team", "caller", "lead", "call"];
  const KEYS = ["range", "v", ...DRILL, ...FILTERS.map((f) => f[0])];

  const WORDS = {
    price: "Price", emi_or_finance: "EMI or finance", time: "No time now", value_doubt: "Doubts the value", trust: "Trust",
    family_approval: "Family approval", course_fit: "Course fit", course_unavailable: "Course not available",
    joined_elsewhere: "Joined elsewhere", job_or_placement: "Job or placement", language: "Language",
    technical: "Technical", not_interested: "Not interested", other: "Other",
    fee_question: "Asked about fees", payment_intent: "Wants to pay", emi_interest: "Asked about EMI",
    start_date: "Asked the start date", enrol_intent: "Wants to enrol", urgency: "Urgency",
    decision_maker_ready: "Decision maker ready", documents_or_details: "Shared details or documents",
    buying_signal_missed: "Buying signal missed", objection_unhandled: "Objection not handled",
    payment_ready: "Ready to pay", payment_friction: "Payment trouble", commitment_made: "Commitment made",
    callback_promised: "Callback promised", question_unanswered: "Question left unanswered",
    misinformation_risk: "Possible wrong information", pressure_excessive: "Too much pressure",
    strong_practice: "Strong practice", possible_not_real: "Possibly not a real conversation",
    zip_disagreement: "Zipteams disagrees",
    buying_vocabulary: "Buying words", key_phrase: "Key phrase", objection_wording: "Objection wording",
    hesitation: "Hesitation", commitment_language: "Commitment words", uncertainty: "Uncertainty",
    persuasive: "Persuasive wording", ineffective_wording: "Wording that hurt",
    ANALYZED: "Analysed", PENDING_ANALYSIS: "Waiting for analysis", ANALYSIS_IN_PROGRESS: "Being analysed",
    ANALYSIS_INCOMPLETE: "Partly analysed", ANALYSIS_FAILED: "Analysis failed (retried)",
    TRANSCRIPT_NOT_FOUND: "No transcript found yet", NO_TRANSCRIPT_EXPECTED: "Not connected (no recording expected)",
    NOT_LOOKED_UP: "Not searched yet", FOUND: "Transcript found", NOT_FOUND: "No recording found",
    NOT_TRANSCRIBED: "Recording without text yet", NO_NUMBER: "No lead number to search", LOOKUP_FAILED: "Search failed (retried)",
    NOT_EXPECTED: "Not expected (not connected)",
    REAL_CALL: "Real call (3+ min)", SHORT_CALL: "Short call (under 3 min)", NOT_CONNECTED: "Not connected",
    UNKNOWN: "Answered, length unknown",
    payment_ready_unconverted: "Ready to pay, not enrolled", link_sent_unpaid: "Payment link sent, not paid",
    missed_callback: "Missed inbound call not returned", unresolved_objection: "Objection still open",
    emi_friction: "EMI trouble", weak_follow_up: "Weak follow-up", high_intent_marked_low: "High intent, marked low",
    repeated_dials_no_conversation: "Many dials, no conversation",
    suspect: "Suspect", pattern: "Pattern", short: "Short",
    under_3_min: "Under 3 min", empty_transcript: "Empty transcript", no_content: "No content", thin: "Thin talk", machine: "Machine or IVR", loop: "Looping",
    duration_mismatch: "Length mismatch", overlap: "Overlapping calls", repeat: "Repeat calls",
    just_over_3_min: "Just over 3 min", no_recording_long_call: "No recording", one_sided: "One-sided",
    not_sales_talk: "Not sales talk",
    VERIFIED: "Verified", VERIFIED_ASSIGNED_BY: "Verified (Assigned By)", UNVERIFIED: "Unverified",
    AUTOMATED: "Automated", CORRECTED: "Corrected",
    questioning: "Questioning", active_listening: "Active listening", objection_handling: "Objection handling",
    product_explanation: "Explaining the course", pricing_explanation: "Explaining the fee",
    payment_guidance: "Guiding the payment", follow_up_discipline: "Follow-up discipline",
    customer_engagement: "Customer engagement", closing: "Closing",
    hot: "Hot", warm: "Warm", cool: "Cool", cold: "Cold", unclear: "Unclear",
    person: "Person", shared: "Shared login", bot: "Automation", not_a_user: "Not a user", team: "Team",
  };
  // Same wording as analytics/convintel/schema.py INTEGRITY_FLAGS; a snapshot's own list wins.
  const FLAG_TEXT = {
    under_3_min: "answered but under 3 minutes", empty_transcript: "a recording exists but its transcript is empty",
    no_content: "almost no words in the transcript",
    thin: "very little talk for the time", machine: "recorded message, IVR or voicemail", loop: "one phrase repeating",
    duration_mismatch: "recording much shorter than the time LeadSquared logged",
    overlap: "caller was on another answered call at the same time", repeat: "3+ real calls to one lead in a day",
    just_over_3_min: "caller's calls bunch just over 3 minutes",
    no_recording_long_call: "a 3+ minute call with no recording or transcript",
    one_sided: "only one side seems to speak", not_sales_talk: "not a sales conversation (wrong number, personal, hold music)",
  };
  const STATUS_TEXT = {
    ANALYZED: "every required analysis is done", PENDING_ANALYSIS: "queued for analysis",
    ANALYSIS_IN_PROGRESS: "being analysed now", ANALYSIS_INCOMPLETE: "some analysis done, the rest still to run",
    ANALYSIS_FAILED: "analysis failed; retried automatically", TRANSCRIPT_NOT_FOUND: "no transcript yet; searched again on a schedule",
    NO_TRANSCRIPT_EXPECTED: "never connected, so there is nothing to transcribe",
  };
  // [label, direction (+1 higher is better, -1 lower is better, 0 neutral), format]
  const M = {
    calls: ["Calls", 0], dials: ["Outbound dials", 0], inbound: ["Inbound calls", 0], connected: ["Answered", 0],
    realCalls: ["Real calls (3+ min)", 1], shortCalls: ["Short calls (under 3 min)", 0],
    unknownCalls: ["Answered, length unknown", 0], notConnected: ["Not connected", 0],
    connectPct: ["Connect rate", 1, "pct"], realRatePct: ["Real calls per answered call", 1, "pct"],
    talkMin: ["Talk time (min)", 1], avgRealMin: ["Average real call (min)", 0],
    workingDays: ["Working days (20+ dials)", 0], realPerWorkingDay: ["Real calls per working day", 1],
    leadsContacted: ["Leads reached", 1], leadsReal: ["Leads with a real call", 1],
    enrolmentsOwner: ["Enrolments (lead owner)", 1], enrolmentsLastCaller: ["Enrolments (last caller)", 1],
    convPerRealPct: ["Enrolled per lead with a real call", 1, "pct"], convPerContactedPct: ["Enrolled per lead reached", 1, "pct"],
    missedInbound: ["Missed inbound, not called back in 2 h", -1], pendingCallbacks: ["Callbacks promised, still open", -1],
    overdueFollowUps: ["Follow-ups overdue (24 h+)", -1], paymentReady: ["Ready-to-pay leads not enrolled", 0],
    highIntentUnconverted: ["High-intent leads not enrolled", 0], qualityAvg: ["Call quality (0-10)", 1],
    transcriptsExpected: ["Transcripts expected", 0], transcriptsFound: ["Transcripts found", 0],
    analyzed: ["Fully analysed", 0], coveragePct: ["Analysed share", 1, "pct"],
    integrityFlagged: ["Possibly not real (calls)", -1], integrityPct: ["Possibly not real (share of answered calls)", -1, "pct"],
    callers: ["Callers", 0], workloadCv: ["Workload spread (0 = even)", 0, "cv"], dialsShare: ["Share of team dials", 0, "pct"],
    revenue: ["Revenue", 1, "money"], revenuePerCaller: ["Revenue per caller", 1, "money"],
  };
  const COMPARE = ["dials", "connected", "connectPct", "realCalls", "realRatePct", "workingDays", "realPerWorkingDay",
    "talkMin", "avgRealMin", "leadsContacted", "leadsReal", "enrolmentsOwner", "enrolmentsLastCaller", "convPerRealPct",
    "convPerContactedPct", "qualityAvg", "coveragePct", "integrityFlagged", "integrityPct", "missedInbound",
    "pendingCallbacks", "overdueFollowUps", "paymentReady", "highIntentUnconverted", "revenue"];

  const sorts = {};

  // ---- formatting -----------------------------------------------------------------------------

  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const isNum = (v) => v !== null && v !== undefined && v !== "" && typeof v !== "boolean" && Number.isFinite(Number(v));
  const fmt = (v, d = 1) => (isNum(v) ? Number(v).toLocaleString("en-IN", { maximumFractionDigits: d }) : "–");
  const pc = (v) => (isNum(v) ? `${fmt(v, 1)}%` : "–");
  const list = (a) => (Array.isArray(a) ? a : []);
  const obj = (o) => (o && typeof o === "object" && !Array.isArray(o) ? o : {});
  const word = (k) => (k === null || k === undefined || k === "" ? "–" : WORDS[k] ?? String(k).replace(/_/g, " "));
  const mmss = (s) => {
    if (!isNum(s)) return "–";
    const t = Math.round(Number(s));
    return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`;
  };
  /** [key, n] pairs from {k: n}, [[k, n]] or [{k|name|team, n}]. */
  const pairsOf = (o) => (Array.isArray(o)
    ? o.map((p) => (Array.isArray(p) ? [p[0], p[1]] : [obj(p).k ?? obj(p).name ?? obj(p).item ?? obj(p).team, obj(p).n ?? obj(p).count]))
    : Object.entries(obj(o)));
  const yes = (b) => (b === true ? "yes" : b === false ? "no" : "–");

  function mval(k, v) {
    const f = (M[k] || [])[2];
    if (f === "money") return isNum(v) ? `₹${fmt(v, 0)}` : "not measurable yet";
    if (f === "pct") return pc(v);
    if (f === "cv") return fmt(v, 2);
    return fmt(v, 1);
  }

  /** A difference against the team or organisation: percentage points for *Pct metrics. */
  function diff(k, d) {
    if (!isNum(d)) return "";
    const n = Number(d), good = (M[k] || [])[1] || 0;
    const cls = !n || !good ? "" : n * good > 0 ? " good" : " bad";
    return ` <span class="ci-diff${cls}">${n > 0 ? "+" : n < 0 ? "−" : "±"}${esc(fmt(Math.abs(n), 1))}${k.endsWith("Pct") ? " pp" : ""}</span>`;
  }

  const istOf = (utc) => {
    const ms = Date.parse(String(utc ?? "").slice(0, 19).replace(" ", "T") + "Z");
    return Number.isFinite(ms) ? new Date(ms + IST_MS).toISOString() : null;
  };
  /** "9 Oct 2026, 12:00 IST" for a UTC 'YYYY-MM-DD HH:MM:SS[ UTC]'. */
  function istTime(utc) {
    const iso = istOf(utc);
    if (!iso) return "not recorded";
    const d = new Date(iso.slice(0, 10) + "T00:00:00Z").toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
    return `${d}, ${iso.slice(11, 16)} IST`;
  }
  /** An IST 'YYYY-MM-DD HH:MM' from the snapshot, labelled. */
  const ist = (s) => (s ? (/IST$/.test(String(s)) ? String(s) : `${s} IST`) : "–");

  const chip = (text, cls = "") => `<span class="chip${cls ? " " + cls : ""}">${esc(text)}</span>`;
  const chips = (items, cls = "") => list(items).map((i) => chip(word(i), cls)).join(" ") || "–";
  /** Claude's word analysis of a call as counts by kind of phrase (no transcript words), plus repeats. */
  const wordsOf = (c) => {
    const parts = pairsOf(c.words).sort((a, b) => b[1] - a[1]).map(([k, n]) => chip(`${word(k)} ${fmt(n, 0)}`));
    if (isNum(c.repeats) && c.repeats > 0) parts.push(chip(`${fmt(c.repeats, 0)} repeated phrase${c.repeats === 1 ? "" : "s"}`));
    return parts.join(" ") || "–";
  };
  const tile = (label, value, sub = "", cls = "") =>
    `<div class="tile${cls ? " " + cls : ""}"><span class="label">${esc(label)}</span><span class="value">${esc(value)}</span>${sub ? `<span class="muted small">${esc(sub)}</span>` : ""}</div>`;
  const tiles = (items) => `<section class="tiles">${items.join("")}</section>`;
  const card = (title, body, cls = "") => `<section class="card${cls ? " " + cls : ""}"><h2>${esc(title)}</h2>${body}</section>`;
  const note = (text) => `<p class="muted small">${esc(text)}</p>`;
  const kv = (rows) => `<dl class="ci-kv">${rows.map(([k, v, html]) => `<dt>${esc(k)}</dt><dd>${html ? v : esc(v ?? "–")}</dd>`).join("")}</dl>`;

  // ---- tables ----------------------------------------------------------------------------------

  function sortValue(c, r) {
    const v = c.v(r);
    if (!c.num) return String(v ?? "").toLowerCase();
    return isNum(v) ? Number(v) : -Infinity;
  }

  /** Sortable table. Column: {h, v: raw value (sorting), f?: text, html?: escaped html, num?, cls?}. */
  function tbl(id, spec, rows, empty = "Nothing to show for this range and these filters.") {
    if (!rows.length) return `<p class="empty">${esc(empty)}</p>`;
    const s = sorts[id];
    let out = rows;
    if (s && spec[s.i]) {
      const c = spec[s.i];
      out = rows.map((r, k) => [sortValue(c, r), k, r])
        .sort((a, b) => (a[0] < b[0] ? -s.dir : a[0] > b[0] ? s.dir : a[1] - b[1])).map((x) => x[2]);
    }
    const head = spec.map((c, i) => {
      const on = s && s.i === i;
      return `<th class="sortable${c.num ? " num" : ""}" data-t="${esc(id)}" data-i="${i}" tabindex="0" title="Sort" ` +
        `aria-sort="${on ? (s.dir === 1 ? "ascending" : "descending") : "none"}">${esc(c.h)}<span class="arrow">${on ? (s.dir === 1 ? " ▲" : " ▼") : ""}</span></th>`;
    }).join("");
    const cell = (c, r) => {
      if (c.html) return c.html(r);
      const v = c.f ? c.f(r) : c.v(r);
      return esc(v === null || v === undefined || v === "" ? "–" : v);
    };
    const body = out.map((r) => `<tr>${spec.map((c) => `<td class="${c.cls || (c.num ? "num" : "")}">${cell(c, r)}</td>`).join("")}</tr>`).join("");
    return `<div class="scroll"><table class="ci-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
  }

  /** A metric column, with the difference against `vs` (vsOrg / vsTeam) when given. */
  const mcol = (h, k, vs) => ({ h, num: true, v: (r) => r[k], html: (r) => esc(mval(k, r[k])) + (vs ? diff(k, obj(r[vs])[k]) : "") });
  const pairTable = (id, h1, h2, pairs, label = word) =>
    tbl(id, [{ h: h1, v: (p) => label(p[0]) }, { h: h2, num: true, v: (p) => p[1], f: (p) => fmt(p[1], 1) }],
      pairs.filter((p) => p[0] !== undefined && p[0] !== null));

  // ---- links and state -------------------------------------------------------------------------

  /** '#...' for a view: the range and filters carry over, drill keys come only from `patch`. */
  function href(st, patch) {
    const p = new URLSearchParams();
    for (const k of KEYS) {
      const v = k === "v" || DRILL.includes(k) ? patch[k] : (k in patch ? patch[k] : st[k]);
      if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
    }
    return "#" + p.toString();
  }
  const link = (x, patch, text) => `<a href="${esc(href(x.st, patch))}">${esc(text)}</a>`;
  const teamLink = (x, team) => (team ? link(x, { v: "team", team }, team) : "–");
  const callerLink = (x, id, name, team) => (id || name ? link(x, { v: "caller", team, caller: id || name }, name || id) : "–");
  const leadLink = (x, id, team, caller) => (id ? link(x, { v: "lead", team, caller, lead: id }, id) : "–");
  const callLink = (x, c, text) => link(x, { v: "call", team: c.team, caller: c.callerId, lead: c.leadId, call: c.callId }, text);
  const callIdLink = (x, id) => {
    const c = x.calls.get(id);
    return c ? callLink(x, c, c.startIst ? ist(c.startIst) : id) : esc(id);
  };

  function options(raw) {
    return list(raw).map((o) => {
      if (Array.isArray(o)) return [String(o[0]), String(o[1] ?? o[0])];
      if (o && typeof o === "object") {
        const v = o.id ?? o.value ?? o.key ?? o.name ?? o.team ?? o.leader;
        return [String(v), String(o.name ?? o.label ?? o.leader ?? o.team ?? v)];
      }
      return [String(o), String(o)];
    }).filter(([v]) => v && v !== "undefined" && v !== "null");
  }

  // ---- the context one render works on --------------------------------------------------------

  function context(snap, st, opts) {
    const x = { snap, st, opts, privacy: obj(snap.privacy) };
    const by = (rows, key) => new Map(list(rows).map((r) => [obj(r)[key], r]));
    x.leads = by(snap.leads, "leadId");
    x.calls = by(snap.calls, "callId");
    x.teams = by(snap.teams, "team");
    x.callers = new Map();
    for (const c of list(snap.callers)) { x.callers.set(c.callerId, c); if (!x.callers.has(c.caller)) x.callers.set(c.caller, c); }
    x.byLead = new Map();
    for (const c of list(snap.calls)) {
      if (!x.byLead.has(c.leadId)) x.byLead.set(c.leadId, []);
      x.byLead.get(c.leadId).push(c);
    }
    x.callerName = (id) => (x.callers.get(id) || {}).caller || list(snap.calls).find((c) => c.callerId === id)?.caller || id;
    x.flagText = { ...FLAG_TEXT, ...obj(obj(snap.definitions).integrityFlags) };
    // LeadSquared's lead record has no team, so a lead's team is usually its owner's team from the caller list.
    x.leadTeam = (l) => l.team || (l.owner && (x.callers.get(l.owner) || {}).team) || null;

    const leaderTeams = st.fleader ? new Set(list(snap.teams).filter((t) => t.teamLeader === st.fleader).map((t) => t.team)) : null;
    const team = (t) => (!st.fteam || t === st.fteam) && (!leaderTeams || leaderTeams.has(t));
    // A team's leads: the ones it owns, called last, or has a listed call on.
    const leadTeamOk = (l) => team(x.leadTeam(l)) || team(l.lastCallerTeam) || (x.byLead.get(l.leadId) || []).some((c) => team(c.team));
    const caller = (id, name) => !st.fcaller || id === st.fcaller || name === st.fcaller;
    const leadAttrs = !!(st.fcourse || st.fstage || st.fprio);
    const leadOk = (id) => {
      if (!leadAttrs) return true;
      const l = x.leads.get(id);
      return !!l && (!st.fcourse || l.course === st.fcourse) && (!st.fstage || l.stage === st.fstage) && (!st.fprio || String(l.priority) === st.fprio);
    };
    const cat = (c) => !st.fcat || list(c.objections).includes(st.fcat) || list(c.signals).includes(st.fcat)
      || list(c.findings).some((f) => obj(f).category === st.fcat);
    const status = (c) => !st.fstatus || c.status === st.fstatus;
    const callLevel = !!(st.fcaller || st.fcat || st.fstatus);
    x.f = {
      active: FILTERS.some(([k]) => st[k]), leadAttrs, team, caller,
      teamRow: (t) => team(t.team),
      callerRow: (c) => team(c.team) && caller(c.callerId, c.caller),
      call: (c) => team(c.team) && caller(c.callerId, c.caller) && leadOk(c.leadId) && cat(c) && status(c),
      lead: (l) => leadTeamOk(l) && leadOk(l.leadId) && (!callLevel
        || (x.byLead.get(l.leadId) || []).some((c) => caller(c.callerId, c.caller) && cat(c) && status(c))
        || (!st.fstatus && (!st.fcaller || caller(l.lastCallerId, l.lastCaller) || l.owner === x.callerName(st.fcaller))
          && (!st.fcat || list(l.repeatedObjections).includes(st.fcat)))),
      opp: (o) => {
        const c = x.calls.get(o.callId);
        return team(o.team) && caller(o.callerId, o.caller) && leadOk(o.leadId)
          && (!st.fcat || o.kind === st.fcat || (!!c && cat(c))) && (!st.fstatus || (!!c && c.status === st.fstatus));
      },
      gap: (g) => team(g.team) && caller(g.callerId, g.caller) && leadOk(g.leadId) && (!st.fstatus || g.status === st.fstatus)
        && (!st.fcat || cat(x.calls.get(g.callId) || {})),
    };
    return x;
  }

  /** "Showing N of M": every capped list says how many it was capped from. */
  function showing(n, listed, total, what, hint = false) {
    const t = isNum(total) ? Number(total) : listed;
    const capped = t > listed;
    let s = n < listed
      ? `Showing ${fmt(n, 0)} of ${fmt(listed, 0)} listed ${what}${capped ? ` (${fmt(t, 0)} in total; the list is capped)` : ""}`
      : `Showing ${fmt(n, 0)} of ${fmt(t, 0)} ${what}${capped ? " (the list is capped)" : ""}`;
    if (hint) s += ". Course, stage and priority come from the lead list, so rows on unlisted leads are hidden while those filters are on";
    return `<p class="muted small ci-count">${esc(s)}.</p>`;
  }

  // ---- page frame ------------------------------------------------------------------------------

  function header(x) {
    const s = x.snap, r = obj(s.range), o = x.opts;
    const genMs = Date.parse(String(s.generatedAt ?? "").slice(0, 19).replace(" ", "T") + "Z");
    const late = o.live && Number.isFinite(genMs) && Date.now() - genMs > LATE_MIN * 60_000;
    const days = r.from && r.to ? (r.from === r.to ? r.from : `${r.from} to ${r.to}`) : "";
    const ranges = list(o.ranges).length
      ? `<nav class="ci-ranges" aria-label="Date range">${list(o.ranges).map((g) =>
        `<button type="button" data-range="${esc(g.key)}" class="${g.key === (o.range || r.key) ? "on" : ""}">${esc(g.label || g.key)}</button>`).join("")}</nav>`
      : "";
    return `<header><div><h1>Conversation Intelligence &amp; Team Analytics</h1>` +
      `<p class="fresh${late ? " late" : ""}">Data as of ${esc(istTime(obj(s.dataAsOf).callsUpTo))} · published ${esc(istTime(s.generatedAt))}</p>` +
      `<p class="muted small">${esc(r.label || r.key || "")}${days ? `: ${esc(days)} (IST days)` : ""}${o.embedded ? " · saved copy, not live" : ""}` +
      `${o.embedded ? "" : ' · <a href="index.html">Main dashboard</a>'}</p></div>` +
      `<div class="range-box">${ranges}</div></header>`;
  }

  function intro(x) {
    const d = obj(x.snap.definitions), cov = obj(x.snap.coverage);
    const secs = isNum(d.realCallSecs) ? Number(d.realCallSecs) : 180;
    const lines = [
      `In this view a real call means answered and ${secs / 60 === Math.round(secs / 60) ? `${secs / 60}+ minutes` : `${secs}+ seconds`}. ` +
      `The main dashboard's "real conversation" (answered, 2+ minutes) is unchanged.`,
    ];
    if (isNum(cov.expected_transcripts)) {
      lines.push(`Analysed so far: ${fmt(cov.analyzed, 0)} of ${fmt(cov.expected_transcripts, 0)} expected transcripts (${pc(cov.coverage_pct)}). ` +
        "Scores, objections and findings cover analysed calls only.");
    }
    if (d.semanticEngine) lines.push(`Transcript analysis: ${d.semanticEngine}.`);
    lines.push(x.privacy.excerpts ? "This copy includes short transcript excerpts: keep it inside the team." : "No transcript text or lead phone numbers are shown.");
    return `<section class="ci-intro">${lines.map((l) => `<p>${esc(l)}</p>`).join("")}</section>`;
  }

  function tabs(x) {
    const cur = DRILL_TAB[x.st.v] || x.st.v || "overview";
    return `<nav class="ci-tabs" aria-label="Views">${VIEWS.map(([k, label]) =>
      `<a href="${esc(href(x.st, { v: k }))}"${k === cur ? ' class="on" aria-current="page"' : ""}>${esc(label)}</a>`).join("")}</nav>`;
  }

  function filterBar(x) {
    const f = obj(x.snap.filters);
    const selects = FILTERS.map(([key, field, all]) => {
      const opts = options(f[field]);
      const cur = x.st[key] || "";
      if (cur && !opts.some(([v]) => v === cur)) opts.unshift([cur, cur]);
      const lbl = ["callers", "teams", "courses", "stages", "teamLeaders"].includes(field) ? (v) => v : word;
      return `<select data-f="${key}" aria-label="${esc(all)}"><option value="">${esc(all)}</option>` +
        opts.map(([v, t]) => `<option value="${esc(v)}"${v === cur ? " selected" : ""}>${esc(lbl(t))}</option>`).join("") + "</select>";
    }).join("");
    return `<section class="card ci-filterbar"><div class="filters">${selects}` +
      `${x.f.active ? '<button type="button" data-clear="1">Clear filters</button>' : ""}</div>` +
      note("Team and caller numbers cover all their calls; course, stage, category, status and priority narrow the lists of leads, calls, gaps and opportunities.") +
      "</section>";
  }

  function crumbs(x) {
    const st = x.st, out = [link(x, { v: "overview" }, "Organisation")];
    if (st.team) out.push(link(x, { v: "team", team: st.team }, st.team));
    if (st.caller) out.push(link(x, { v: "caller", team: st.team, caller: st.caller }, x.callerName(st.caller)));
    if (st.lead) out.push(link(x, { v: "lead", team: st.team, caller: st.caller, lead: st.lead }, `Lead ${st.lead}`));
    if (st.call) {
      const c = x.calls.get(st.call);
      out.push(esc(`Call ${c && c.startIst ? ist(c.startIst) : st.call}`));
    }
    return `<nav class="ci-crumbs" aria-label="Drilldown">${out.join(' <span aria-hidden="true">›</span> ')}</nav>`;
  }

  // ---- shared pieces ---------------------------------------------------------------------------

  const people = (rows) => list(rows).filter((r) => !r.kind || r.kind === "person" || r.kind === "team");
  const others = (rows) => list(rows).filter((r) => r.kind && r.kind !== "person" && r.kind !== "team");

  function teamsTable(x, id, rows) {
    return tbl(id, [
      { h: "Team", v: (t) => t.team, html: (t) => teamLink(x, t.team) },
      { h: "Team leader", v: (t) => t.teamLeader, html: (t) => esc(t.teamLeader || "(not recorded)") +
        (t.teamLeaderSource && !["config", "not recorded"].includes(t.teamLeaderSource) ? ` <span class="muted small">(${esc(t.teamLeaderSource)})</span>` : "") },
      mcol("Callers", "callers"), mcol("Dials", "dials"), mcol("Connect rate", "connectPct", "vsOrg"),
      mcol("Real calls (3+ min)", "realCalls"), mcol("Real / working day", "realPerWorkingDay", "vsOrg"),
      mcol("Real of answered", "realRatePct", "vsOrg"), mcol("Talk min", "talkMin"),
      mcol("Enrolled (owner)", "enrolmentsOwner"), mcol("Enrolled (last caller)", "enrolmentsLastCaller"),
      mcol("Enrolled per real-call lead", "convPerRealPct", "vsOrg"), mcol("Quality", "qualityAvg", "vsOrg"),
      mcol("Analysed", "coveragePct", "vsOrg"), mcol("Possibly not real", "integrityPct", "vsOrg"),
      mcol("Missed inbound", "missedInbound"), mcol("Overdue follow-ups", "overdueFollowUps"), mcol("Workload spread", "workloadCv"),
    ], rows);
  }

  function callersTable(x, id, rows) {
    return tbl(id, [
      { h: "Caller", v: (c) => c.caller, html: (c) => callerLink(x, c.callerId, c.caller, c.team) },
      { h: "Team", v: (c) => c.team, html: (c) => teamLink(x, c.team) },
      mcol("Dials", "dials"), mcol("Share of team dials", "dialsShare"), mcol("Connect rate", "connectPct", "vsTeam"),
      mcol("Working days", "workingDays"), mcol("Real calls (3+ min)", "realCalls"),
      mcol("Real / working day", "realPerWorkingDay", "vsTeam"), mcol("Real of answered", "realRatePct", "vsTeam"),
      mcol("Talk min", "talkMin"), mcol("Avg real min", "avgRealMin", "vsTeam"),
      mcol("Enrolled (last caller)", "enrolmentsLastCaller"), mcol("Enrolled per real-call lead", "convPerRealPct", "vsTeam"),
      mcol("Quality", "qualityAvg", "vsTeam"), mcol("Possibly not real", "integrityPct", "vsTeam"),
      mcol("Callbacks open", "pendingCallbacks"), mcol("Overdue follow-ups", "overdueFollowUps"),
    ], rows);
  }

  function othersTable(x, id, rows, what) {
    if (!rows.length) return "";
    return card(`${what}: in organisation totals, never ranked`, tbl(id, [
      { h: "Name", v: (r) => r.caller || r.team }, { h: "Kind", v: (r) => word(r.kind) },
      mcol("Calls", "calls"), mcol("Dials", "dials"), mcol("Inbound", "inbound"), mcol("Answered", "connected"),
      mcol("Real calls (3+ min)", "realCalls"), mcol("Missed inbound", "missedInbound"),
    ], rows) + note("Shared admin logins are used by several people and automation is not a person, so their calls count in the organisation's totals and coverage but not in any caller ranking."));
  }

  function leadsTable(x, id, rows) {
    return tbl(id, [
      { h: "Lead", v: (l) => l.leadId, html: (l) => leadLink(x, l.leadId, x.leadTeam(l), x.st.caller) },
      { h: "Owner", v: (l) => l.owner }, { h: "Owner's team", v: (l) => x.leadTeam(l) },
      { h: "Last caller", v: (l) => l.lastCaller, html: (l) => (l.lastCaller ? callerLink(x, l.lastCallerId, l.lastCaller, l.lastCallerTeam) : "–") },
      { h: "Stage", v: (l) => l.stage },
      { h: "Course", v: (l) => l.course }, { h: "Priority", v: (l) => l.priority },
      { h: "Calls", num: true, v: (l) => l.calls, f: (l) => fmt(l.calls, 0) },
      { h: "Real calls (3+ min)", num: true, v: (l) => l.realCalls, f: (l) => fmt(l.realCalls, 0) },
      { h: "Last call", v: (l) => l.lastCallIst, f: (l) => ist(l.lastCallIst) },
      { h: "Readiness", num: true, v: (l) => l.readiness, f: (l) => (isNum(l.readiness) ? `${fmt(l.readiness, 0)} ${l.readinessBand ? word(l.readinessBand).toLowerCase() : ""} (${l.readinessTrend || "–"})` : "–") },
      { h: "Same objection again", v: (l) => list(l.repeatedObjections).join(" "), html: (l) => chips(l.repeatedObjections) },
      { h: "Missed promises", num: true, v: (l) => l.missedCommitments, f: (l) => fmt(l.missedCommitments, 0) },
      { h: "Flags", cls: "flags", v: (l) => list(l.flags).join("; ") },
      { h: "Next step", cls: "flags", v: (l) => l.nextAction },
      { h: "Enrolled", v: (l) => yes(l.enrolled) },
    ], rows);
  }

  function callsTable(x, id, rows) {
    return tbl(id, [
      { h: "Call (IST)", v: (c) => c.startIst, html: (c) => callLink(x, c, ist(c.startIst || c.callId)) },
      { h: "Caller", v: (c) => c.caller, html: (c) => callerLink(x, c.callerId, c.caller, c.team) },
      { h: "Team", v: (c) => c.team }, { h: "Lead", v: (c) => c.leadId, html: (c) => leadLink(x, c.leadId, c.team, c.callerId) },
      { h: "Length", num: true, v: (c) => c.durationS, f: (c) => mmss(c.durationS) },
      { h: "Class", v: (c) => word(c.class) }, { h: "Status", v: (c) => word(c.status) },
      { h: "Readiness", num: true, v: (c) => c.readiness, f: (c) => (isNum(c.readiness) ? `${fmt(c.readiness, 0)} ${c.readinessBand || ""}` : "–") },
      { h: "Quality", num: true, v: (c) => quality(c).overall, f: (c) => (isNum(quality(c).overall) ? `${fmt(quality(c).overall, 1)}/10` : "–") },
      { h: "Objections", v: (c) => list(c.objections).join(" "), html: (c) => chips(c.objections) },
      { h: "Signals", v: (c) => list(c.signals).join(" "), html: (c) => chips(c.signals) },
      { h: "Possibly not real", v: (c) => flagsOf(c).join(" "), html: (c) => chips(flagsOf(c), "lagging") },
      { h: "Zipteams", v: (c) => c.zipIntent, f: (c) => zipText(c) },
    ], rows);
  }

  const quality = (c) => (isNum(c.quality) ? { overall: Number(c.quality) } : obj(c.quality));
  /** Why a call may not be real: the export's own reasons, else the flag's meaning; plus Claude's finding. */
  const reasonsOf = (x, c) => {
    const given = list(c.integrityReasons);
    const flags = flagsOf(c).map((f, i) => given[i] || x.flagText[f] || word(f));
    return [...flags, ...list(c.findings).filter((f) => obj(f).category === "possible_not_real").map((f) => f.reasoning)].filter(Boolean);
  };
  const flagsOf = (c) => list(c.integrityFlags).map((f) => (f && typeof f === "object" ? f.flag : f)).filter(Boolean);
  const zipText = (c) => (!c.zipIntent || c.zipIntent === "NOT_AVAILABLE" ? "no Zipteams note"
    : `${c.zipIntent}${c.zipAgrees === true ? " · agrees" : c.zipAgrees === false ? " · disagrees" : ""}`);

  function compareTable(id, row, vsKey, vsLabel) {
    const prior = row.prior && typeof row.prior === "object" ? row.prior : null;
    const keys = COMPARE.filter((k) => k in row);
    return tbl(id, [
      { h: "Measure", v: (k) => (M[k] || [k])[0] },
      { h: "This period", num: true, v: (k) => row[k], f: (k) => mval(k, row[k]) },
      { h: vsLabel, num: true, v: (k) => obj(row[vsKey])[k], html: (k) => diff(k, obj(row[vsKey])[k]).trim() || "–" },
      { h: "Previous period", num: true, v: (k) => (prior ? prior[k] : null), f: (k) => (prior ? mval(k, prior[k]) : "not compared") },
    ], keys);
  }

  function qualityTable(id, rows) {
    const dims = [...new Set(rows.flatMap(([, q]) => Object.keys(obj(q))))];
    if (!dims.length || rows.every(([, q]) => Object.values(obj(q)).every((v) => !isNum(v)))) {
      return note("No quality scores yet: they come from Claude's reading, and Claude has not scored these calls yet.");
    }
    return tbl(id, [{ h: "Skill", v: (d) => word(d) }, ...rows.map(([name, q]) =>
      ({ h: name, num: true, v: (d) => obj(q)[d], f: (d) => (isNum(obj(q)[d]) ? `${fmt(obj(q)[d], 1)}/10` : "–") }))], dims);
  }

  function findingsList(x, findings) {
    const rows = list(findings).map(obj);
    if (!rows.length) return `<p class="empty">No findings for this call.</p>`;
    return `<ul class="ci-findings">${rows.map((f) => `<li><div>${chip(word(f.category))} ${chip(`${f.confidence || "unclear"} confidence`, f.confidence === "high" ? "ok" : "")}</div>` +
      `<p>${esc(f.reasoning || "")}</p>` +
      (f.action || f.recommended_action ? `<p><strong>Next step:</strong> ${esc(f.action || f.recommended_action)}</p>` : "") +
      (x.privacy.excerpts && f.excerpt ? `<blockquote class="ci-excerpt">${esc(f.excerpt)}</blockquote>` : "") +
      "</li>").join("")}</ul>`;
  }

  function coachingBlock(x, co) {
    const c = obj(co);
    const items = (a) => (list(a).length ? `<ul>${list(a).map((i) => `<li>${esc(typeof i === "object" ? `${i.item}${isNum(i.n) ? ` (${fmt(i.n, 0)} calls)` : ""}` : i)}</li>`).join("")}</ul>` : `<p class="empty">None yet.</p>`);
    return `<div class="ci-cols"><div><h3>Strengths</h3>${items(c.strengths)}</div><div><h3>To improve</h3>${items(c.weaknesses)}</div>` +
      `<div><h3>Next actions</h3>${items(c.actions)}</div></div>` + `<h3>Weekly sample: 5 longest real calls</h3>${weekly(x, c.weeklySample)}`;
  }

  function weekly(x, ws) {
    const groups = Array.isArray(ws) ? (ws.length ? [["", ws]] : []) : Object.entries(obj(ws)).sort((a, b) => (a[0] < b[0] ? 1 : -1));
    if (!groups.length) return `<p class="empty">No real calls to sample.</p>`;
    return groups.map(([wk, ids]) => `<p>${wk ? `<span class="muted small">Week from ${esc(wk)}:</span> ` : ""}${list(ids).map((id) => callIdLink(x, id)).join(", ")}</p>`).join("") +
      note("For a manual listen in addition to the full analysis, never instead of it.");
  }

  function notListed(what) {
    return card(`This ${what} is not in the list`, note(`The snapshot lists the most relevant ${what}s for this range, capped to keep the page fast. Pick another range or use the lists.`));
  }

  // ---- views -----------------------------------------------------------------------------------

  const V = {};

  V.overview = (x) => {
    const o = obj(x.snap.org), rev = obj(x.snap.revenue);
    const teams = people(x.snap.teams).filter(x.f.teamRow);
    return tiles([
      tile("Calls", fmt(o.calls, 0), `${fmt(o.dials, 0)} outbound dials`),
      tile("Connect rate", pc(o.connectPct), "answered outbound / dials"),
      tile("Real calls (3+ min)", fmt(o.realCalls, 0), `${pc(o.realRatePct)} of answered calls`),
      tile("Leads with a real call", fmt(o.leadsReal, 0), `of ${fmt(o.leadsContacted, 0)} leads reached`),
      tile("Enrolments", fmt(o.enrolmentsOwner, 0), isNum(obj(o.enrolmentsUnattributed).ownerAtEnrolment)
        ? `${fmt(obj(o.enrolmentsUnattributed).ownerAtEnrolment, 0)} not credited to a team` : "first-time, this period"),
      tile("Call quality", isNum(o.qualityAvg) ? `${fmt(o.qualityAvg, 1)}/10` : "–", isNum(o.qualityAvg) ? "calls scored by Claude" : "not scored yet"),
      tile("Analysed", pc(o.coveragePct), `${fmt(o.analyzed, 0)} of ${fmt(o.transcriptsExpected, 0)} transcripts`),
      tile("Possibly not real", fmt(o.integrityFlagged, 0), `${pc(o.integrityPct)} of answered calls · needs review`, "warn"),
      tile("Revenue", isNum(o.revenue) ? mval("revenue", o.revenue) : rev.measurable && isNum(rev.total) ? `₹${fmt(rev.total, 0)}` : "–",
        isNum(o.revenue) || (rev.measurable && isNum(rev.total)) ? "from payment records" : "no payment records with an amount yet"),
    ]) + tiles([
      tile("Missed inbound, not called back", fmt(o.missedInbound, 0), "within 2 hours", "bad"),
      tile("Follow-ups overdue", fmt(o.overdueFollowUps, 0), "promised, 24 h+ ago", "warn"),
      tile("Callbacks still open", fmt(o.pendingCallbacks, 0)),
      tile("Ready to pay, not enrolled", fmt(o.paymentReady, 0), "leads"),
      tile("High intent, not enrolled", fmt(o.highIntentUnconverted, 0), "leads"),
    ]) + card("Teams", note("Each rate shows the difference from the organisation (pp = percentage points). Click a team to drill down.") +
      teamsTable(x, "ov-teams", teams), "") +
    `<div class="grid">` +
      card("Objections heard", pairTable("ov-obj", "Objection", "Calls", pairsOf(obj(x.snap.objections).org).sort((a, b) => b[1] - a[1]))) +
      card("Words and phrases", note("From Claude's reading of each call: calls with at least one phrase of each kind.") +
        pairTable("ov-words", "Kind of phrase", "Calls", pairsOf(obj(x.snap.words).org).sort((a, b) => b[1] - a[1]))) +
      card("Quality by skill (0-10)", qualityTable("ov-q", [["Organisation", o.qualityByDim], ...teams.map((t) => [t.team, t.qualityByDim])]), "wide") +
      card("How to read these numbers", `<ul class="ci-notes">${list(obj(x.snap.definitions).notes).map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`, "wide") +
    "</div>";
  };

  const notYet = (r) => r.unanalyzed ?? (isNum(r.expected_transcripts) && isNum(r.analyzed) ? r.expected_transcripts - r.analyzed : null);
  const noTranscript = (r) => r.missing_transcripts ?? obj(r.by_status).TRANSCRIPT_NOT_FOUND;

  V.coverage = (x) => {
    const c = obj(x.snap.coverage);
    const gaps = list(c.gaps).filter(x.f.gap);
    const recon = list(x.snap.reconciliation);
    const covCols = (key, label) => [
      { h: label, v: (r) => r[key], html: key === "team" ? (r) => teamLink(x, r.team) : undefined },
      { h: "Calls", num: true, v: (r) => r.total_calls, f: (r) => fmt(r.total_calls, 0) },
      { h: "Transcripts expected", num: true, v: (r) => r.expected_transcripts, f: (r) => fmt(r.expected_transcripts, 0) },
      { h: "Found", num: true, v: (r) => r.transcripts_found, f: (r) => fmt(r.transcripts_found, 0) },
      { h: "Analysed", num: true, v: (r) => r.analyzed, f: (r) => fmt(r.analyzed, 0) },
      { h: "Not yet", num: true, v: notYet, f: (r) => fmt(notYet(r), 0) },
      { h: "No transcript yet", num: true, v: noTranscript, f: (r) => fmt(noTranscript(r), 0) },
      { h: "Analysed %", num: true, v: (r) => r.coverage_pct, f: (r) => pc(r.coverage_pct) },
    ];
    return tiles([
      tile("Calls in range", fmt(c.total_calls, 0)), tile("Transcripts expected", fmt(c.expected_transcripts, 0), "every connected call"),
      tile("Transcripts found", fmt(c.transcripts_found, 0)), tile("Fully analysed", fmt(c.analyzed, 0), pc(c.coverage_pct)),
      tile("Not analysed yet", fmt(c.unanalyzed, 0), "", "warn"), tile("No transcript yet", fmt(c.missing_transcripts, 0), "searched again on a schedule"),
      tile("Retries waiting", fmt(c.pending_retries, 0)),
    ]) + `<div class="grid">` +
      card("Every call has one status", tbl("cov-status", [
        { h: "Status", v: (p) => word(p[0]) }, { h: "Calls", num: true, v: (p) => p[1], f: (p) => fmt(p[1], 0) },
        { h: "Meaning", cls: "flags", v: (p) => STATUS_TEXT[p[0]] || "" },
      ], pairsOf(c.by_status)) + note(c.reconciles === false ? "The statuses do NOT add up to the calls: see the checks." : "The statuses add up to every call in the range.")) +
      card("Call types", pairTable("cov-class", "Type", "Calls", pairsOf(c.by_class))) +
      card("Transcript search", pairTable("cov-tstate", "State", "Calls", pairsOf(c.by_transcript_state))) +
      card("Checks before trusting coverage", tbl("cov-recon", [
        { h: "Result", v: (r) => (r.ok ? "OK" : "Check"), html: (r) => chip(r.ok ? "OK" : "Check", r.ok ? "ok" : "lagging") },
        { h: "Check", v: (r) => r.check }, { h: "Detail", cls: "flags", v: (r) => r.detail },
      ], recon), "wide") +
      card("By team", tbl("cov-team", covCols("team", "Team"), list(c.byTeam).filter((r) => x.f.team(r.team))), "wide") +
      card("By day (IST)", tbl("cov-day", covCols("day", "Day"), list(c.byDay)), "wide") +
    "</div>" + card("Calls not analysed yet, with the reason and next retry",
      showing(gaps.length, list(c.gaps).length, c.gapsTotal, "calls", x.f.leadAttrs) + tbl("cov-gaps", [
        { h: "Call (IST)", v: (g) => g.startIst, html: (g) => (x.calls.has(g.callId) ? callIdLink(x, g.callId) : esc(ist(g.startIst || g.callId))) },
        { h: "Caller", v: (g) => g.caller, html: (g) => callerLink(x, g.callerId, g.caller, g.team) }, { h: "Team", v: (g) => g.team },
        { h: "Lead", v: (g) => g.leadId, html: (g) => leadLink(x, g.leadId, g.team, g.callerId) },
        { h: "Type", v: (g) => word(g.class) }, { h: "Status", v: (g) => word(g.status) },
        { h: "Why", cls: "flags", v: (g) => g.reason }, { h: "Tries", num: true, v: (g) => g.attempts, f: (g) => fmt(g.attempts, 0) },
        { h: "Next retry", v: (g) => g.nextRetryIst, f: (g) => (g.nextRetryIst ? ist(g.nextRetryIst) : "no automatic retry") },
      ], gaps));
  };

  V.teams = (x) => {
    const rows = people(x.snap.teams).filter(x.f.teamRow);
    return card("Teams compared with the organisation", note("Differences are against the organisation (pp = percentage points). Unique-lead counts do not add up across teams: a lead called by two teams counts in each.") +
      teamsTable(x, "teams", rows)) + othersTable(x, "teams-other", others(x.snap.teams).filter(x.f.teamRow), "Shared logins, automation and non-users");
  };

  V.callers = (x) => {
    const rows = people(x.snap.callers).filter(x.f.callerRow);
    return card("Callers compared with their team", note("Differences are against the caller's own team. Working day = 20+ outbound dials.") +
      `<p class="muted small ci-count">${esc(`${fmt(rows.length, 0)} of ${fmt(people(x.snap.callers).length, 0)} callers`)}</p>` +
      callersTable(x, "callers", rows)) + othersTable(x, "callers-other", others(x.snap.callers).filter(x.f.callerRow), "Shared logins and automation");
  };

  V.leads = (x) => {
    const all = list(x.snap.leads), rows = all.filter(x.f.lead);
    return card("Leads: where each one stands and the next step", showing(rows.length, all.length, x.snap.leadsTotal, "leads") + leadsTable(x, "leads", rows));
  };

  V.calls = (x) => {
    const all = list(x.snap.calls), rows = all.filter(x.f.call);
    return card("Calls", showing(rows.length, all.length, x.snap.callsTotal, "calls", x.f.leadAttrs) +
      note("Click a call for its transcript intelligence: readiness, quality, objections, signals and findings.") + callsTable(x, "calls", rows));
  };

  V.integrity = (x) => {
    const g = obj(x.snap.integrity);
    const rows = list(g.byCaller).filter(x.f.callerRow);
    // The calls the "Flagged calls" tile counts: every flag on a call that is not a short call is a suspect or
    // pattern flag (analytics/convintel/integrity.py); short calls are counted on their own.
    const listed = list(x.snap.calls).filter((c) => c.class !== "SHORT_CALL" && flagsOf(c).length > 0);
    const flagged = listed.filter(x.f.call);
    return `<div class="alert">${esc("A flag means the call needs a listen, not that it was faked. Check the recording before acting on it.")}</div>` +
      tiles([
        tile("Flagged calls", fmt(g.flaggedCalls, 0), "real calls that may not be real conversations", "warn"),
        tile("Short calls", fmt(g.shortCalls, 0), "answered, under 3 minutes"),
        ...pairsOf(g.byTier).map(([t, n]) => tile(`${word(t)} tier`, fmt(n, 0))),
      ]) + card("Per caller", tbl("int-callers", [
        { h: "Caller", v: (r) => r.caller, html: (r) => callerLink(x, r.callerId, r.caller, r.team) },
        { h: "Team", v: (r) => r.team }, { h: "Kind", v: (r) => word(r.kind || "person") },
        { h: "Answered", num: true, v: (r) => r.calls, f: (r) => fmt(r.calls, 0) },
        { h: "Real calls (3+ min)", num: true, v: (r) => r.realCalls, f: (r) => fmt(r.realCalls, 0) },
        { h: "Short", num: true, v: (r) => r.shortCalls, f: (r) => fmt(r.shortCalls, 0) },
        { h: "Short %", num: true, v: (r) => r.shortPct, f: (r) => pc(r.shortPct) },
        { h: "Flagged", num: true, v: (r) => r.flagged, f: (r) => fmt(r.flagged, 0) },
        { h: "Flagged % of real calls", num: true, v: (r) => r.flaggedPct, f: (r) => pc(r.flaggedPct) },
        { h: "Reasons", cls: "flags", v: (r) => pairsOf(r.byFlag).map((p) => p[0]).join(" "),
          html: (r) => pairsOf(r.byFlag).filter((p) => p[1]).map(([f, n]) => `<span class="chip" title="${esc(x.flagText[f] || "")}">${esc(`${word(f)} ${fmt(n, 0)}`)}</span>`).join(" ") || "–" },
        { h: "Calls to review", cls: "flags", v: (r) => r.callIdsTotal, html: (r) => {
          const ids = list(r.callIds), total = isNum(r.callIdsTotal) ? Number(r.callIdsTotal) : ids.length;
          const shown = Math.min(ids.length, ROW_IDS);
          if (!shown) return total ? esc(`${fmt(total, 0)} (not listed)`) : "–";
          return ids.slice(0, ROW_IDS).map((id) => callIdLink(x, id)).join(", ") +
            (total > shown ? ` <span class="muted small">${esc(`showing ${shown} of ${fmt(total, 0)}`)}</span>` : "");
        } },
      ], rows) + note("People first, most flagged first. Shared logins and automation are listed by kind and never checked for overlap, repeats or bunching.")) +
      card("What each flag means", tbl("int-flags", [
        { h: "Flag", v: (p) => word(p[0]) }, { h: "Meaning", cls: "flags", v: (p) => x.flagText[p[0]] || "" },
        { h: "Calls", num: true, v: (p) => p[1], f: (p) => fmt(p[1], 0) },
      ], pairsOf(g.byFlag))) +
      card("Flagged calls", showing(flagged.length, listed.length, g.flaggedCalls, "flagged calls", x.f.leadAttrs) + tbl("int-calls", [
        { h: "Call (IST)", v: (c) => c.startIst, html: (c) => callLink(x, c, ist(c.startIst || c.callId)) },
        { h: "Caller", v: (c) => c.caller, html: (c) => callerLink(x, c.callerId, c.caller, c.team) }, { h: "Team", v: (c) => c.team },
        { h: "Length", num: true, v: (c) => c.durationS, f: (c) => mmss(c.durationS) },
        { h: "Flags", v: (c) => flagsOf(c).join(" "), html: (c) => chips(flagsOf(c), "lagging") },
        { h: "Why it may not be real", cls: "flags", v: (c) => reasonsOf(x, c).join(" ") },
      ], flagged)) +
      (list(g.notes).length ? card("Notes", `<ul class="ci-notes">${list(g.notes).map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`) : "");
  };

  V.opportunities = (x) => {
    const all = list(x.snap.opportunities), rows = all.filter(x.f.opp);
    return `<div class="grid">` + card("By kind", pairTable("opp-kinds", "Kind", "Leads", pairsOf(x.snap.opportunitiesByKind).sort((a, b) => b[1] - a[1]))) + "</div>" +
      card("Leads to act on, most valuable first", showing(rows.length, all.length, x.snap.opportunitiesTotal, "opportunities", x.f.leadAttrs) + tbl("opps", [
        { h: "Kind", v: (o) => word(o.kind) },
        { h: "Lead", v: (o) => o.leadId, html: (o) => leadLink(x, o.leadId, o.team, o.callerId) },
        { h: "Caller", v: (o) => o.caller, html: (o) => callerLink(x, o.callerId, o.caller, o.team) }, { h: "Team", v: (o) => o.team },
        { h: "Day", v: (o) => o.day }, { h: "Why", cls: "flags", v: (o) => o.evidence },
        { h: "Next step", cls: "flags", v: (o) => o.nextAction }, { h: "Confidence", v: (o) => o.confidence },
        { h: "Call", v: (o) => o.callId, html: (o) => (o.callId ? callIdLink(x, o.callId) : "–") },
      ], rows));
  };

  V.zip = (x) => {
    const z = obj(x.snap.zip), b = obj(z.benchmark);
    const ex = list(z.examples).filter((e) => x.f.team(e.team) && x.f.caller(e.callerId, e.caller));
    const group = (k, label) => {
      const g = obj(b[k]);
      return [label, g.leads ?? g.n, g.enrolled, g.pct ?? g.enrolledPct, g.smallSample ?? b.smallSample];
    };
    const bench = [group("zipHigh", "Zipteams said high"), group("ourHigh", "Ours said high (same calls)"),
      group("ourHighAllCalls", "Ours said high (all analysed calls)")].filter((r) => r[1] !== undefined);
    return `<div class="alert">${esc("Zipteams intent is one input, never the answer. Teams without Zipteams notes have no baseline; that is not a disagreement.")}</div>` +
      tiles([
        tile("Calls compared", fmt(z.compared, 0), isNum(z.analysed) ? `of ${fmt(z.analysed, 0)} analysed` : ""),
        tile("Agree", fmt(z.agree, 0), isNum(z.agreePct) ? pc(z.agreePct) : ""), tile("Disagree", fmt(z.disagree, 0), "needs a listen", "warn"),
        tile("No Zipteams baseline", fmt(z.noBaseline, 0), "analysed calls without a Zipteams note"),
        ...(isNum(z.unsupportedHot) ? [tile("Zipteams high, no buying signal", fmt(z.unsupportedHot, 0))] : []),
        ...(isNum(z.missedHot) ? [tile("Zipteams low, ours high", fmt(z.missedHot, 0))] : []),
        ...(isNum(z.ourUnclear) ? [tile("Too thin for our reading", fmt(z.ourUnclear, 0), "not compared")] : []),
      ]) + card("By team", tbl("zip-teams", [
        { h: "Team", v: (r) => r.team, html: (r) => teamLink(x, r.team) },
        { h: "Compared", num: true, v: (r) => r.compared, f: (r) => fmt(r.compared, 0) },
        { h: "Agree", num: true, v: (r) => r.agree, f: (r) => fmt(r.agree, 0) },
        { h: "Disagree", num: true, v: (r) => r.disagree, f: (r) => fmt(r.disagree, 0) },
        { h: "No baseline", num: true, v: (r) => r.noBaseline, f: (r) => fmt(r.noBaseline, 0) },
        { h: "Agree %", num: true, v: (r) => r.agreePct, f: (r) => (r.baseline === false || (!r.compared && !isNum(r.agreePct)) ? "no Zipteams baseline" : pc(r.agreePct)) },
        { h: "Note", cls: "flags", v: (r) => r.note },
      ], list(z.byTeam).filter((r) => x.f.team(r.team)))) +
      card("Disagreements to look at", showing(ex.length, list(z.examples).length, z.examplesTotal ?? z.disagree, "disagreements") + tbl("zip-ex", [
        { h: "Call", v: (e) => e.callId, html: (e) => callIdLink(x, e.callId) },
        { h: "Caller", v: (e) => e.caller, html: (e) => callerLink(x, e.callerId, e.caller, e.team) }, { h: "Team", v: (e) => e.team },
        { h: "Zipteams", v: (e) => e.zipIntent || e.zipLevel }, { h: "Ours", v: (e) => e.ourLevel,
          f: (e) => `${e.ourLevel || "–"}${isNum(e.ourReadiness ?? e.ourScore) ? ` (${fmt(e.ourReadiness ?? e.ourScore, 0)})` : ""}${e.engine ? `, ${e.engine}` : ""}` },
        { h: "Kind", v: (e) => (e.kind === "unsupported_hot" ? "Zipteams high, no buying signal" : e.kind === "missed_hot" ? "Zipteams low, ours high" : e.kind ? "levels differ" : "") },
        { h: "What drove ours", v: (e) => list(e.signals).join(" "), html: (e) => chips(e.signals) },
      ], ex)) +
      card("Which view predicts enrolment better", (bench.length ? tbl("zip-bench", [
        { h: "Leads", v: (r) => r[0] }, { h: "Leads", num: true, v: (r) => r[1], f: (r) => fmt(r[1], 0) },
        { h: "Later enrolled", num: true, v: (r) => r[2], f: (r) => fmt(r[2], 0) },
        { h: "Enrolled %", num: true, v: (r) => r[3], f: (r) => pc(r[3]) },
        { h: "Sample", v: (r) => (r[4] ? "small: not reliable yet" : "") },
      ], bench) : `<p class="empty">No benchmark yet.</p>`) +
        (bench.some((r) => r[4]) ? `<div class="alert">${esc("Small sample (under 30 leads in a group): not a fair test yet.")}</div>` : "") + (b.note ? note(b.note) : "")) +
      (list(z.notes).length ? card("Notes", `<ul class="ci-notes">${list(z.notes).map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`) : "");
  };

  V.coaching = (x) => {
    const teams = list(x.snap.coachingTeams).filter((t) => x.f.team(t.team));
    const callers = people(x.snap.callers).filter(x.f.callerRow);
    const items = (a, fmtI) => (list(a).length ? `<ul>${list(a).map((i) => `<li>${fmtI(i)}</li>`).join("")}</ul>` : `<p class="empty">Nothing yet.</p>`);
    const who = (a) => (list(a).length ? `: ${list(a).join(", ")}` : "");
    return teams.map((t) => card(`Team: ${t.team}${isNum(t.callers) ? ` (${fmt(t.callers, 0)} callers)` : ""}`, `<div class="ci-cols">` +
      `<div><h3>Gaps shared by 2+ callers</h3>${items(t.gaps, (g) => esc(`${g.item} (${Array.isArray(g.callers) ? g.callers.join(", ") : `${fmt(g.callers, 0)} callers`}, ${fmt(g.n, 0)} calls)${who(g.who)}`))}</div>` +
      `<div><h3>What works</h3>${items(t.practices, (p) => esc(`${p.item}${isNum(p.n) ? ` (${fmt(p.n, 0)} calls)` : ""}${who(p.from)}`))}</div>` +
      `<div><h3>Priorities</h3>${items(t.priorities, (p) => esc(p))}</div></div>`)).join("") +
      card("Callers", tbl("coach-callers", [
        { h: "Caller", v: (c) => c.caller, html: (c) => callerLink(x, c.callerId, c.caller, c.team) }, { h: "Team", v: (c) => c.team },
        { h: "Strengths", cls: "flags", v: (c) => list(obj(c.coaching).strengths).map((i) => i.item).join("; ") },
        { h: "To improve", cls: "flags", v: (c) => list(obj(c.coaching).weaknesses).map((i) => i.item).join("; ") },
        { h: "Next actions", cls: "flags", v: (c) => list(obj(c.coaching).actions).join(" ") },
        { h: "Weekly sample", cls: "flags", v: (c) => list(obj(c.coaching).weeklySample).length,
          html: (c) => {
            const ws = obj(c.coaching).weeklySample;
            const ids = Array.isArray(ws) ? ws : Object.values(obj(ws)).flat();
            return ids.slice(0, ROW_IDS).map((id) => callIdLink(x, id)).join(", ") || "–";
          } },
      ], callers) + note("The weekly sample is the 5 longest real calls per caller per week (Monday to Sunday, IST), for a manual listen in addition to the full analysis. Shared logins and automation get no coaching."));
  };

  V.accountability = (x) => {
    const a = obj(x.snap.accountability);
    return tiles([tile("Lead actions checked", fmt(a.rows, 0)), tile("Unverified", fmt(a.unverified, 0), "not charged to anyone", "warn")]) +
      `<div class="grid">` + card("By status", pairTable("acc-status", "Status", "Actions", pairsOf(a.byStatus))) +
      card("By person", pairTable("acc-person", "Person", "Actions", pairsOf(a.byPerson), (k) => k)) +
      (a.byAction ? card("By action", pairTable("acc-action", "Action", "Leads", pairsOf(a.byAction), (k) => k)) : "") + "</div>" +
      card("How it is credited", `<ul class="ci-notes">${list(a.notes).map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`);
  };

  // The snapshot's "revenue" section is analytics/convintel/revenue.py revenue_section(): enrolments are always
  // credited two ways; total and amounts are null until payment records carry a readable amount.
  V.revenue = (x) => {
    const r = obj(x.snap.revenue), e = obj(r.enrolments);
    const am = r.measurable && r.amounts && typeof r.amounts === "object" ? r.amounts : null;
    const views = [["ownerAtEnrolment", "lead owner at enrolment"], ["lastAnsweredCaller", "last answered caller"]];
    const byTeam = (src, k) => obj(obj(obj(src).byTeam)[k]);
    const teams = [...new Set(views.flatMap(([k]) => [...Object.keys(byTeam(e, k)), ...Object.keys(byTeam(am, k))]))].filter((t) => x.f.team(t)).sort();
    const reasons = (src, what) => views.flatMap(([k, label]) =>
      pairsOf(obj(obj(obj(src).unattributedReasons)[k])).map(([why, n]) => [label, why, n, what]));
    const why = [...reasons(e, "enrolments"), ...reasons(am, "payments")];
    // Per person: byCallerDetail {view: [{callerId, caller, team, enrolments, revenue}]}; byCaller {view: {name: n}} if not.
    const merged = new Map();
    for (const [k] of views) {
      const detail = obj(r.byCallerDetail)[k];
      const rows = Array.isArray(detail) ? detail.map(obj) : pairsOf(obj(r.byCaller)[k]).map(([caller, n]) => ({ caller, enrolments: n }));
      for (const p of rows) {
        const key = p.callerId || p.caller;
        const m = merged.get(key) || { caller: p.caller, team: p.team };
        m[k] = p.enrolments;
        m[`${k}Rev`] = p.revenue;
        merged.set(key, m);
      }
    }
    const people = [...merged.values()].filter((m) => x.f.team(m.team));
    const f = obj(r.fields);
    const fieldRows = list(f.fields).map(obj);
    const money = (v) => (isNum(v) ? `₹${fmt(v, 0)}` : "–");
    const unN = (src, k) => obj(obj(src).unattributed)[k];
    return (r.measurable ? "" : `<div class="alert"><strong>Revenue: not measurable yet.</strong> ${esc(r.reason || "No payment records for this period.")}</div>`) +
      tiles([
        tile("Revenue", r.measurable && isNum(r.total) ? money(r.total) : "not measurable yet",
          r.measurable ? (r.amountField ? `from the field ${r.amountField}` : "") : "enrolments shown instead"),
        tile("Payment records", fmt(r.paymentEvents, 0), am ? `${fmt(am.payments, 0)} with an amount, ${fmt(am.withoutAmount, 0)} without` : ""),
        tile("First-time enrolments", fmt(e.total, 0)),
        ...views.filter(([k]) => isNum(unN(e, k))).map(([k, label]) => tile(`Enrolments not credited (${label})`, fmt(unN(e, k), 0))),
        ...(am ? views.filter(([k]) => isNum(unN(am, k))).map(([k, label]) => tile(`Revenue not credited (${label})`, money(unN(am, k)))) : []),
      ]) + (r.measurable && r.reason ? note(r.reason) : "") +
      card("Enrolments by team, two ways", note("Each enrolment counts once in each column: by the lead owner's team at enrolment, and by the team of the last person with an answered call at or before it.") +
        tbl("rev-teams", [
          { h: "Team", v: (t) => t, html: (t) => teamLink(x, t) },
          { h: "Enrolled (owner)", num: true, v: (t) => byTeam(e, "ownerAtEnrolment")[t], f: (t) => fmt(byTeam(e, "ownerAtEnrolment")[t] ?? 0, 0) },
          { h: "Enrolled (last caller)", num: true, v: (t) => byTeam(e, "lastAnsweredCaller")[t], f: (t) => fmt(byTeam(e, "lastAnsweredCaller")[t] ?? 0, 0) },
          ...(am ? [{ h: "Revenue (owner)", num: true, v: (t) => byTeam(am, "ownerAtEnrolment")[t], f: (t) => money(byTeam(am, "ownerAtEnrolment")[t]) },
            { h: "Revenue (last caller)", num: true, v: (t) => byTeam(am, "lastAnsweredCaller")[t], f: (t) => money(byTeam(am, "lastAnsweredCaller")[t]) }] : []),
        ], teams)) +
      (people.length ? card("Enrolments by person", tbl("rev-callers", [
        { h: "Person", v: (m) => m.caller }, { h: "Team", v: (m) => m.team },
        { h: "As lead owner", num: true, v: (m) => m.ownerAtEnrolment, f: (m) => fmt(m.ownerAtEnrolment ?? 0, 0) },
        { h: "As last caller", num: true, v: (m) => m.lastAnsweredCaller, f: (m) => fmt(m.lastAnsweredCaller ?? 0, 0) },
        ...(am ? [{ h: "Revenue (owner)", num: true, v: (m) => m.ownerAtEnrolmentRev, f: (m) => money(m.ownerAtEnrolmentRev) },
          { h: "Revenue (last caller)", num: true, v: (m) => m.lastAnsweredCallerRev, f: (m) => money(m.lastAnsweredCallerRev) }] : []),
      ], people)) : "") +
      (why.length ? card("Why some are not credited", tbl("rev-why", [
        { h: "Counted", v: (w) => w[3] }, { h: "View", v: (w) => w[0] }, { h: "Reason", cls: "flags", v: (w) => w[1] },
        { h: "Records", num: true, v: (w) => w[2], f: (w) => fmt(w[2], 0) },
      ], why)) : "") +
      (fieldRows.length ? card("Payment fields LeadSquared sends (names and types only)", tbl("rev-fields", [
        { h: "Field", v: (g) => g.field },
        { h: "Present", num: true, v: (g) => g.present, f: (g) => fmt(g.present, 0) },
        { h: "Filled", num: true, v: (g) => g.filled, f: (g) => fmt(g.filled, 0) },
        { h: "Types", v: (g) => pairsOf(g.types).map(([t, n]) => `${t} ${fmt(n, 0)}`).join(", ") },
      ], fieldRows) + (list(f.amountCandidates).length ? note(`Fields that look like an amount: ${list(f.amountCandidates).map((c) =>
        `${obj(c).field} (${fmt(obj(c).readable, 0)} readable)`).join(", ")}.`) : "") + (f.note ? note(f.note) : "")) : "") +
      (list(r.notes).length ? card("How revenue is counted", `<ul class="ci-notes">${list(r.notes).map((n) => `<li>${esc(n)}</li>`).join("")}</ul>`) : "");
  };

  // ---- drilldown pages -------------------------------------------------------------------------

  V.team = (x) => {
    const t = x.teams.get(x.st.team);
    if (!t) return notListed("team");
    const callers = list(x.snap.callers).filter((c) => c.team === t.team);
    const coach = list(x.snap.coachingTeams).find((c) => c.team === t.team);
    const zip = list(obj(x.snap.zip).byTeam).find((r) => r.team === t.team);
    return card(t.team, kv([
      ["Team leader", `${t.teamLeader || "(not recorded)"}${t.teamLeaderSource && t.teamLeaderSource !== "not recorded" ? ` (from the ${t.teamLeaderSource === "config" ? "team leader list" : "team name"})` : ""}`],
      ["Callers", fmt(t.callers, 0)], ["Kind", word(t.kind || "team")],
      ["Zipteams", zip ? (zip.baseline === false ? "no Zipteams baseline" : `${fmt(zip.agree, 0)} agree, ${fmt(zip.disagree, 0)} disagree of ${fmt(zip.compared, 0)}`) : "–"],
    ])) + card("Compared with the organisation", compareTable("team-cmp", t, "vsOrg", "vs organisation")) +
      card("Callers in this team", note("Differences are against this team. Click a caller to drill down.") + callersTable(x, "team-callers", people(callers))) +
      othersTable(x, "team-others", others(callers), "Shared logins and automation") +
      `<div class="grid">` + card("Objections heard", pairTable("team-obj", "Objection", "Calls", pairsOf(obj(obj(x.snap.objections).byTeam)[t.team] || t.objections).sort((a, b) => b[1] - a[1]))) +
      card("Words and phrases", pairTable("team-words", "Kind of phrase", "Calls", pairsOf(obj(obj(x.snap.words).byTeam)[t.team]).sort((a, b) => b[1] - a[1]))) +
      card("Quality by skill (0-10)", qualityTable("team-q", [[t.team, t.qualityByDim], ["Organisation", obj(x.snap.org).qualityByDim]])) + "</div>" +
      (coach ? card("Team coaching", `<ul class="ci-notes">${list(coach.priorities).map((p) => `<li>${esc(p)}</li>`).join("") || "<li>No priorities yet.</li>"}</ul>`) : "");
  };

  V.caller = (x) => {
    const c = list(x.snap.callers).find((k) => (k.callerId === x.st.caller || k.caller === x.st.caller) && (!x.st.team || k.team === x.st.team))
      || x.callers.get(x.st.caller);
    if (!c) return notListed("caller");
    const calls = list(x.snap.calls).filter((k) => k.callerId === c.callerId && (!k.team || !c.team || k.team === c.team));
    const leadIds = new Set(calls.map((k) => k.leadId));
    const leads = list(x.snap.leads).filter((l) => leadIds.has(l.leadId) || l.lastCallerId === c.callerId || l.owner === c.caller);
    const ig = obj(c.integrity);
    const person = !c.kind || c.kind === "person";
    return card(c.caller, kv([["Team", teamLink(x, c.team), true], ["Kind", word(c.kind || "person")],
      ["Possibly not real", `${fmt(ig.flagged, 0)} calls (${pc(ig.flaggedPct)} of real calls; needs review, not proof)`],
      ["Flag reasons", pairsOf(ig.byFlag).filter((p) => p[1]).map(([f, n]) => `${word(f)} ${fmt(n, 0)}`).join(", ") || "–"]]) +
      (person ? "" : note("A shared login or automation account: shown for completeness, never ranked or coached."))) +
      card("Compared with the team", compareTable("caller-cmp", c, "vsTeam", "vs team")) +
      (person ? card("Coaching", coachingBlock(x, c.coaching)) : "") +
      card("Leads this caller worked", showing(leads.length, leads.length, null, "leads in the list") + leadsTable(x, "caller-leads", leads)) +
      card("Calls", showing(calls.length, calls.length, null, "calls in the list") + callsTable(x, "caller-calls", calls));
  };

  V.lead = (x) => {
    const l = x.leads.get(x.st.lead);
    const calls = (x.byLead.get(x.st.lead) || []).slice().sort((a, b) => String(a.startIst).localeCompare(String(b.startIst)));
    const opps = list(x.snap.opportunities).filter((o) => o.leadId === x.st.lead);
    if (!l && !calls.length) return notListed("lead");
    const head = l ? card(`Lead ${l.leadId}`, `<p class="ci-next"><strong>Next step:</strong> ${esc(l.nextAction || "–")}</p>` + kv([
      ["Owner", l.owner], ["Owner's team", teamLink(x, x.leadTeam(l)), true], ["Stage", l.stage], ["Course", l.course], ["Priority", l.priority],
      ["LeadSquared score", fmt(l.score, 0)], ["Calls", `${fmt(l.calls, 0)} (${fmt(l.realCalls, 0)} real)`], ["Last call", ist(l.lastCallIst)],
      ["Readiness", isNum(l.readiness) ? `${fmt(l.readiness, 0)}/100, ${l.readinessTrend || "–"}${l.readinessSource ? ` (${l.readinessSource})` : ""}` : "–"],
      ["Same objection again", chips(l.repeatedObjections), true], ["Missed promises", fmt(l.missedCommitments, 0)],
      ["Flags", list(l.flags).join("; ") || "–"], ["Enrolled", yes(l.enrolled)],
    ])) : notListed("lead");
    return head + card("Calls on this lead", callsTable(x, "lead-calls", calls)) +
      (opps.length ? card("Opportunities", tbl("lead-opps", [
        { h: "Kind", v: (o) => word(o.kind) }, { h: "Why", cls: "flags", v: (o) => o.evidence },
        { h: "Next step", cls: "flags", v: (o) => o.nextAction }, { h: "Confidence", v: (o) => o.confidence },
      ], opps)) : "");
  };

  V.call = (x) => {
    const c = x.calls.get(x.st.call);
    if (!c) return notListed("call");
    const q = quality(c), dims = Object.entries(obj(q.byDim || q.dims || q.dimensions));
    const flags = flagsOf(c);
    const unread = c.status !== "ANALYZED" && !c.summary;
    return card("Call", kv([
      ["Caller", callerLink(x, c.callerId, c.caller, c.team), true], ["Team", teamLink(x, c.team), true],
      ["Lead", leadLink(x, c.leadId, c.team, c.callerId), true], ["Start", ist(c.startIst)], ["Length", mmss(c.durationS)],
      ["Direction", c.direction || "–"], ["Type", word(c.class)],
      ["Analysis", `${word(c.status)}${c.statusReason ? `: ${c.statusReason}` : ""}`],
      ["Transcript", c.transcriptState ? word(c.transcriptState) : "–"],
      ["Zipteams", zipText(c)],
    ])) + card("Transcript intelligence", tiles([
      tile("Readiness to pay", isNum(c.readiness) ? `${fmt(c.readiness, 0)}/100` : "–",
        `${c.readinessBand ? word(c.readinessBand) : "this call only"}${c.engine ? " · Claude's reading" : ""}`),
      tile("Call quality", isNum(q.overall) ? `${fmt(q.overall, 1)}/10` : "–", isNum(q.overall) ? "scored by Claude" : "not scored yet"),
      tile("Objections", fmt(list(c.objections).length, 0)), tile("Buying signals", fmt(list(c.signals).length, 0)),
    ]) + (unread ? note("Claude has not read this call yet, so readiness, quality, findings and the summary are blank.") : "") +
      kv([["Objections", chips(c.objections), true], ["Buying signals", chips(c.signals), true],
        ["Words and phrases", wordsOf(c), true],
        ["Possibly not real", flags.length ? reasonsOf(x, c).map(esc).join("<br>") + "<br>" + `<span class="muted small">${esc("Needs a listen, not proof.")}</span>` : "no flags", true],
        ["Summary", c.summary || "No summary yet (written when Claude reads the call)."]]) +
      (dims.length ? qualityTable("call-q", [["This call", Object.fromEntries(dims)]]) : "") +
      `<h3>Findings</h3>${findingsList(x, c.findings)}` +
      (x.privacy.excerpts ? "" : note("Transcript excerpts are not included in this copy.")));
  };

  // ---- render ----------------------------------------------------------------------------------

  /** The whole page for one snapshot and view state, as escaped HTML. */
  function html(snapshot, state = {}, opts = {}) {
    const snap = obj(snapshot);
    const st = { ...state };
    if (!V[st.v]) st.v = st.call ? "call" : st.lead ? "lead" : st.caller ? "caller" : st.team ? "team" : "overview";
    const x = context(snap, st, opts);
    const drill = DRILL.includes(st.v);
    const msg = opts.message ? `<div class="alert${opts.messageBad ? " bad" : ""}">${esc(opts.message)}</div>` : "";
    return header(x) + msg + intro(x) + tabs(x) + (drill ? crumbs(x) : filterBar(x)) + `<div class="ci-view">${V[st.v](x)}</div>`;
  }

  /** A page with no snapshot: the header, the range buttons and why there is nothing to show. */
  function shell(opts = {}) {
    const ranges = list(opts.ranges).map((g) => `<button type="button" data-range="${esc(g.key)}" class="${g.key === opts.range ? "on" : ""}">${esc(g.label || g.key)}</button>`).join("");
    return `<header><div><h1>Conversation Intelligence &amp; Team Analytics</h1><p class="muted small"><a href="index.html">Main dashboard</a></p></div>` +
      `<div class="range-box">${ranges ? `<nav class="ci-ranges" aria-label="Date range">${ranges}</nav>` : ""}</div></header>` +
      `<div class="alert${opts.messageBad ? " bad" : ""}">${esc(opts.message || "Loading…")}</div>`;
  }

  /** Draw `snapshot` into `root` and wire its controls. opts: {state, ranges, range, onRange, message, live, embedded}. */
  function render(snapshot, root, opts = {}) {
    const st = opts.state || readHash();
    root.innerHTML = snapshot ? html(snapshot, st, opts) : shell(opts);
    const again = (s) => render(snapshot, root, { ...opts, state: s });
    root.onchange = (e) => {
      const key = e.target && e.target.dataset ? e.target.dataset.f : "";
      if (!key) return;
      const s = { ...st, [key]: e.target.value };
      writeHash(s);
      again(s);
    };
    const sortBy = (th) => {
      const id = th.dataset.t, i = Number(th.dataset.i), cur = sorts[id];
      const first = th.classList.contains("num") ? -1 : 1;   // numbers biggest first, text A to Z
      sorts[id] = { i, dir: cur && cur.i === i ? -cur.dir : first };
      again(st);
    };
    root.onclick = (e) => {
      const t = e.target;
      if (!t || !t.closest) return;
      const b = t.closest("button[data-range]");
      if (b && opts.onRange) return opts.onRange(b.dataset.range);
      if (t.closest("button[data-clear]")) {
        const s = { ...st };
        for (const [k] of FILTERS) delete s[k];
        writeHash(s);
        return again(s);
      }
      const th = t.closest("th[data-t]");
      if (th) sortBy(th);
    };
    root.onkeydown = (e) => {
      const th = e.target && e.target.closest ? e.target.closest("th[data-t]") : null;
      if (th && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); sortBy(th); }
    };
  }

  function readHash() {
    if (typeof location === "undefined") return {};
    const p = new URLSearchParams(location.hash.slice(1)), s = {};
    for (const k of KEYS) { const v = p.get(k); if (v) s[k] = v; }
    return s;
  }

  function writeHash(s) {
    if (typeof location === "undefined") return;
    const h = href(s, s);
    try { history.replaceState(null, "", h); } catch { location.hash = h; }
  }

  // ---- loading (live page) ---------------------------------------------------------------------

  function boot(root) {
    const app = { snap: null, ranges: [], range: "", message: "", bad: false, gen: "", seq: 0, embedded: false };
    const paint = () => render(app.snap, root, { state: readHash(), ranges: app.ranges, range: app.range, onRange: pick,
      message: app.message, messageBad: app.bad, live: !app.embedded, embedded: app.embedded });
    const embedded = document.getElementById("ci-data");
    if (embedded) {
      app.embedded = true;
      try { app.snap = JSON.parse(embedded.textContent || "null"); } catch { app.message = "The data saved in this page could not be read."; app.bad = true; }
      if (!app.snap && !app.message) { app.message = "This page has no data saved in it."; app.bad = true; }
      window.addEventListener("hashchange", paint);
      return paint();
    }

    async function getJson(path) {
      const r = await fetch(path);
      const text = await r.text();
      let body = null;
      try { body = JSON.parse(text); } catch {}
      if (!r.ok || !body) throw Object.assign(new Error((body && body.error) || `HTTP ${r.status}`), { status: r.status });
      return { body, gen: r.headers.get("x-ci-generated-at") || "" };
    }

    async function loadRanges() {
      try {
        app.ranges = list((await getJson("/api/ci/ranges")).body.ranges);
        return true;
      } catch (e) {
        app.message = e.status ? e.message : `Could not load data: ${e.message}`;
        app.bad = e.status !== 503;
        paint();
        return false;
      }
    }

    async function load(key) {
      const seq = ++app.seq;
      app.range = key;
      if (!app.snap || obj(app.snap.range).key !== key) { app.message = "Loading…"; app.bad = false; paint(); }
      try {
        const { body, gen } = await getJson(`/api/ci/snapshot?range=${encodeURIComponent(key)}`);
        if (seq !== app.seq) return;
        Object.assign(app, { snap: body, range: key, gen, message: "", bad: false });
      } catch (e) {
        if (seq !== app.seq) return;
        // A load half-way through (503) keeps the copy on screen and tries again; anything else explains itself.
        if (!app.snap || obj(app.snap.range).key !== key) app.snap = null;
        app.message = e.status ? e.message : `Could not load data: ${e.message}`;
        app.bad = e.status !== 503;
        if (e.status === 503) setTimeout(() => { if (app.range === key) load(key); }, RETRY_MS);
      }
      paint();
    }

    function pick(key) {
      try { localStorage.setItem("ci-range", key); } catch {}
      writeHash({ ...readHash(), range: key });
      load(key);
    }

    async function start() {
      if (!(await loadRanges())) return;
      const st = readHash();
      let key = st.range;
      if (!app.ranges.some((r) => r.key === key)) {
        let saved = "";
        try { saved = localStorage.getItem("ci-range") || ""; } catch {}
        key = (app.ranges.find((r) => r.key === saved) || app.ranges.find((r) => r.key === "today") || app.ranges[0] || {}).key;
      }
      if (!key) {
        app.message = "No conversation intelligence data has been loaded yet. It appears here once the analysis job publishes its first snapshot.";
        return paint();
      }
      writeHash({ ...st, range: key });
      load(key);
    }

    window.addEventListener("hashchange", () => {
      const key = readHash().range;
      if (key && key !== app.range && app.ranges.some((r) => r.key === key)) load(key); else paint();
    });
    // Snapshots change only when the offline job publishes; fetch again only when the index says so.
    setInterval(async () => {
      if (document.hidden || !app.range) return;
      if (!(await loadRanges())) return;
      const cur = app.ranges.find((r) => r.key === app.range);
      if (cur && cur.generatedAt !== app.gen) load(app.range); else paint();
    }, REFRESH_MS);
    start();
  }

  const api = { render, html, shell, VIEWS: VIEWS.map((v) => v[0]) };
  if (typeof window !== "undefined") window.CI = api;
  if (typeof document !== "undefined" && document.getElementById) {
    const root = document.getElementById("ci-root");
    if (root) boot(root);
  }
})();
