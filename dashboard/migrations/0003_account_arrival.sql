-- Leads put into a shared admin account (e.g. Rinku Jhala's), and who actually did it (src/accountability.ts).
-- One row per arrival, a few dozen a day. first_* are written once and never updated, so a later
-- re-attribution (Assigned By filled in, a correction) keeps what was shown before.
CREATE TABLE account_arrival (
  lead_id           TEXT NOT NULL,
  at                TEXT NOT NULL,       -- UTC 'YYYY-MM-DD HH:MM:SS' of the owner change (or creation)
  day               TEXT NOT NULL,       -- IST day of `at`
  account_id        TEXT NOT NULL,
  how               TEXT NOT NULL,       -- 'owner change' | 'created there'
  login             TEXT NOT NULL,       -- the login the CRM recorded
  put_by            TEXT NOT NULL,       -- the person accountable, or 'Unverified' / 'System (automation)'
  status            TEXT NOT NULL,       -- Verified | Verified (Assigned By) | Unverified | Automated
  assigned_by_field TEXT NOT NULL,
  possible          TEXT NOT NULL,       -- a name to check (stale Assigned By); not proof
  first_put_by      TEXT NOT NULL,
  first_status      TEXT NOT NULL,
  updated_at        TEXT NOT NULL,
  PRIMARY KEY (lead_id, at)
) WITHOUT ROWID;
