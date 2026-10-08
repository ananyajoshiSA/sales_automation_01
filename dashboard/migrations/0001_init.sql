-- Sales dashboard schema, sized for the Cloudflare free tier.
-- Only daily totals are stored (no per-call rows), and every table is WITHOUT ROWID with its
-- primary key as the only index, because each index update counts as an extra D1 row written.
-- Days and hours are IST.

-- Where each ingest task has got to. Written in the same batch as the task's data,
-- so a run that dies part-way leaves both untouched and simply retries.
CREATE TABLE sync_state (
  task       TEXT PRIMARY KEY,
  cursor     TEXT NOT NULL,          -- UTC 'YYYY-MM-DD HH:MM:SS'
  page       INTEGER NOT NULL DEFAULT 1,
  updated_at TEXT NOT NULL,
  last_error TEXT
) WITHOUT ROWID;

-- D1 rows written per UTC day (free tier: 100,000/day, resets 00:00 UTC).
CREATE TABLE write_budget (
  day  TEXT PRIMARY KEY,
  rows INTEGER NOT NULL
) WITHOUT ROWID;

-- LeadSquared users that belong to a team (first MemberOfGroups entry). Refreshed daily.
CREATE TABLE users (
  id         TEXT PRIMARY KEY,
  name       TEXT NOT NULL,
  team       TEXT NOT NULL,
  updated_at TEXT NOT NULL
) WITHOUT ROWID;

-- Per caller per IST day.
CREATE TABLE caller_day (
  day            TEXT NOT NULL,
  user_id        TEXT NOT NULL,
  name           TEXT NOT NULL,
  dials          INTEGER NOT NULL DEFAULT 0,
  answered       INTEGER NOT NULL DEFAULT 0,
  not_answered   INTEGER NOT NULL DEFAULT 0,
  failures       INTEGER NOT NULL DEFAULT 0,
  real_calls     INTEGER NOT NULL DEFAULT 0,   -- answered and >= 120 s
  talk_secs      INTEGER NOT NULL DEFAULT 0,
  inbound        INTEGER NOT NULL DEFAULT 0,
  inbound_missed INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, user_id)
) WITHOUT ROWID;

-- Account-wide outbound dials per IST hour (for the data-gap alarm).
CREATE TABLE calls_hour (
  day      TEXT NOT NULL,
  hour     INTEGER NOT NULL,
  dials    INTEGER NOT NULL DEFAULT 0,
  answered INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, hour)
) WITHOUT ROWID;

-- New leads per IST day, source and owner (team is joined from users at read time).
CREATE TABLE lead_day (
  day      TEXT NOT NULL,
  source   TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  n        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, source, owner_id)
) WITHOUT ROWID;

-- First-time enrollments (first ever stage change to 'Course Enrolled'); re-tags never appear.
CREATE TABLE enrollment (
  lead_id  TEXT PRIMARY KEY,
  day      TEXT NOT NULL,
  at       TEXT NOT NULL,
  owner_id TEXT NOT NULL,
  set_by   TEXT NOT NULL
) WITHOUT ROWID;

-- Latest answered call per lead, used to attribute Zipteams notes (which are created by
-- 'Admin', not the caller). Pruned to 3 days.
CREATE TABLE lead_last_call (
  lead_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  at      TEXT NOT NULL
) WITHOUT ROWID;

-- Zipteams AI notes per caller per IST day. Scores are pass/fail (0 or 100), so sum/n = % of calls.
CREATE TABLE zip_day (
  day             TEXT NOT NULL,
  user_id         TEXT NOT NULL,                -- '' = could not attribute
  analysed        INTEGER NOT NULL DEFAULT 0,
  pitch_n         INTEGER NOT NULL DEFAULT 0,
  pitch_sum       INTEGER NOT NULL DEFAULT 0,
  probe_n         INTEGER NOT NULL DEFAULT 0,
  probe_sum       INTEGER NOT NULL DEFAULT 0,
  obj_n           INTEGER NOT NULL DEFAULT 0,
  obj_sum         INTEGER NOT NULL DEFAULT 0,
  intent_rated    INTEGER NOT NULL DEFAULT 0,
  intent_high     INTEGER NOT NULL DEFAULT 0,
  intent_moderate INTEGER NOT NULL DEFAULT 0,
  intent_low      INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, user_id)
) WITHOUT ROWID;
