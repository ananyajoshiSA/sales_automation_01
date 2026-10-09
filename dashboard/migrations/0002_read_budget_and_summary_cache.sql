-- D1 rows read per UTC day (free tier: 5,000,000/day, resets 00:00 UTC = 05:30 IST).
-- Cron runs and computed summaries add to it; summaries pause at READ_BUDGET.
CREATE TABLE read_budget (
  day  TEXT PRIMARY KEY,
  rows INTEGER NOT NULL
) WITHOUT ROWID;

-- The last computed /api/summary body per date range, served as-is (no parse) for
-- SUMMARY_TTL_SECONDS, and as a stale copy while summaries are paused. Pruned daily.
CREATE TABLE summary_cache (
  range_key   TEXT PRIMARY KEY,        -- 'YYYY-MM-DD|YYYY-MM-DD' (IST from|to)
  computed_at TEXT NOT NULL,           -- UTC 'YYYY-MM-DD HH:MM:SS'
  rows_read   INTEGER NOT NULL,
  body        TEXT NOT NULL
) WITHOUT ROWID;

-- Every summary reads enrollments by day; without this index each one scanned the whole table
-- (all history). Enrollments are ~100 rows a day, so the extra index write per row is negligible.
CREATE INDEX enrollment_day ON enrollment (day);
