// In-memory D1 stand-in for tests, on node:sqlite with the real migrations. meta.rows_read is
// approximated as rows returned (SELECT) and meta.rows_written as rows changed.
import { readdirSync, readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";

const MIGRATIONS = new URL("../migrations/", import.meta.url);
const READS = /^\s*(SELECT|WITH)\b/i;

export function fakeD1() {
  const db = new DatabaseSync(":memory:");
  for (const f of readdirSync(MIGRATIONS).filter((f) => f.endsWith(".sql")).sort()) {
    db.exec(readFileSync(new URL(f, MIGRATIONS), "utf8"));
  }
  const log = [];
  const exec = (sql, args) => {
    log.push(sql);
    const st = db.prepare(sql);
    if (READS.test(sql)) {
      const results = st.all(...args);
      return { success: true, results, meta: { rows_read: results.length, rows_written: 0 } };
    }
    const r = st.run(...args);
    return { success: true, results: [], meta: { rows_read: 0, rows_written: Number(r.changes) } };
  };
  const stmt = (sql, args = []) => ({
    sql, args,
    bind: (...a) => stmt(sql, a),
    all: async () => exec(sql, args),
    run: async () => exec(sql, args),
    first: async (col) => { const row = exec(sql, args).results[0] ?? null; return col ? (row?.[col] ?? null) : row; },
    raw: async () => exec(sql, args).results.map((r) => Object.values(r)),
  });
  return {
    sqlite: db, log,
    prepare: (sql) => stmt(sql),
    batch: async (stmts) => {
      db.exec("BEGIN");
      try { const out = stmts.map((s) => exec(s.sql, s.args)); db.exec("COMMIT"); return out; }
      catch (e) { db.exec("ROLLBACK"); throw e; }
    },
    exec: async (sql) => { db.exec(sql); return { count: 1, duration: 0 }; },
  };
}
