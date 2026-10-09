-- Conversation intelligence (public/ci.html). The analysis runs offline (analytics/convintel) and
-- analytics/convintel/d1push.py loads one precomputed JSON snapshot per date range, split into parts
-- under 90 KB (D1 caps a statement at 100 KB). The Worker only reads these tables (src/ci.ts): no
-- per-call rows, a few dozen rows written per load.

-- One row per part of a snapshot; the parts joined in order are the snapshot's JSON text.
CREATE TABLE IF NOT EXISTS ci_snapshot (
  range_key    TEXT NOT NULL,          -- 'today' | 'yesterday' | '7d' | '30d' | 'YYYY-MM-DD' (IST)
  part         INTEGER NOT NULL,       -- 1..parts
  parts        INTEGER NOT NULL,
  generated_at TEXT NOT NULL,          -- UTC 'YYYY-MM-DD HH:MM:SS'; equal on every part of one load
  body         TEXT NOT NULL,
  PRIMARY KEY (range_key, part)
) WITHOUT ROWID;

-- The snapshots loaded, for the page's range buttons.
CREATE TABLE IF NOT EXISTS ci_snapshot_index (
  range_key    TEXT PRIMARY KEY,
  label        TEXT NOT NULL,
  day_from     TEXT NOT NULL,          -- IST 'YYYY-MM-DD', inclusive
  day_to       TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  parts        INTEGER NOT NULL,
  bytes        INTEGER NOT NULL        -- UTF-8 size of the whole snapshot
) WITHOUT ROWID;
