export interface FakeD1 extends D1Database {
  sqlite: { prepare(sql: string): { all(...a: unknown[]): any[]; run(...a: unknown[]): unknown } };
  log: string[];
}
export function fakeD1(): FakeD1;
