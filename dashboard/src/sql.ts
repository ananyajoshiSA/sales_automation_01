// SQL text builders. D1 allows only 100 bound parameters per query, so multi-row writes
// inline escaped literals instead, split so each statement stays well under the 100 KB limit.

export function str(v: unknown): string {
  return "'" + String(v ?? "").replace(/'/g, "''") + "'";
}

export function int(v: unknown): string {
  const n = Math.round(Number(v));
  return Number.isFinite(n) ? String(n) : "0";
}

const MAX_STATEMENT_BYTES = 90_000;

/** ``INSERT INTO table (cols) VALUES ... <tail>`` split into statements under the size limit. */
export function multiInsert(table: string, cols: string[], rows: string[][], tail = ""): string[] {
  const head = `INSERT INTO ${table} (${cols.join(",")}) VALUES `;
  const out: string[] = [];
  let parts: string[] = [];
  let size = head.length + tail.length;
  for (const r of rows) {
    const v = "(" + r.join(",") + ")";
    if (parts.length && size + v.length + 1 > MAX_STATEMENT_BYTES) {
      out.push(head + parts.join(",") + " " + tail);
      parts = [];
      size = head.length + tail.length;
    }
    parts.push(v);
    size += v.length + 1;
  }
  if (parts.length) out.push(head + parts.join(",") + " " + tail);
  return out;
}

/** ``ON CONFLICT(keys) DO UPDATE SET c = c + excluded.c`` for counter columns. */
export function addOnConflict(keys: string[], counters: string[], replace: string[] = []): string {
  const sets = [
    ...replace.map((c) => `${c} = excluded.${c}`),
    ...counters.map((c) => `${c} = ${c} + excluded.${c}`),
  ];
  return `ON CONFLICT(${keys.join(",")}) DO UPDATE SET ${sets.join(", ")}`;
}
