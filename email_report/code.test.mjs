// Tests for Code.gs against fake Google services: nothing is emailed and no Google account is used.
//     node --test email_report/code.test.mjs
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import vm from 'node:vm';

const CODE = readFileSync(new URL('./Code.gs', import.meta.url), 'utf8');
const TOKEN = 'x'.repeat(40);
const FILES = {
  'team_calling_report_2026-10-11.pdf': Buffer.from('%PDF-1.7\nfake report\n%%EOF'),
  'call_plan_Elite_2026-10-12.xlsx': Buffer.from('PK\u0003\u0004 fake workbook'),
  'callers.csv': Buffer.from('caller,dials\nA,40\n'),
};
const PDF = 'team_calling_report_2026-10-11.pdf';

const signed = (buf) => [...buf].map((b) => (b > 127 ? b - 256 : b));
const unsigned = (arr) => Buffer.from(arr.map((b) => (b + 256) % 256));
const sha = (buf) => createHash('sha256').update(buf).digest('hex');
const pad = (n) => String(n).padStart(2, '0');
const MON = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function formatDate(date, tz, fmt) {
  assert.equal(tz, 'Asia/Kolkata');
  const d = new Date(date.getTime() + 330 * 60000);
  const [y, m, day, h, min] = [d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate(), d.getUTCHours(), d.getUTCMinutes()];
  return {
    'yyyy-MM-dd': `${y}-${pad(m + 1)}-${pad(day)}`,
    'yyyy-MM-dd HH:mm': `${y}-${pad(m + 1)}-${pad(day)} ${pad(h)}:${pad(min)}`,
    'd MMM yyyy, h:mm a': `${day} ${MON[m]} ${y}, ${h % 12 || 12}:${pad(min)} ${h < 12 ? 'AM' : 'PM'}`,
  }[fmt];
}

function store(init = {}) {
  const s = { ...init };
  return {
    data: s,
    getProperties: () => ({ ...s }),
    getProperty: (k) => (k in s ? s[k] : null),
    setProperty: (k, v) => { s[k] = String(v); },
    deleteProperty: (k) => { delete s[k]; },
  };
}

function load(settings = {}, { mailFails = 0 } = {}) {
  const sent = [], logs = [];
  const script = store(settings), user = store();
  const ctx = {
    console: { log: (s) => logs.push(s), error: (s) => logs.push(s) },
    PropertiesService: { getScriptProperties: () => script, getUserProperties: () => user },
    LockService: { getScriptLock: () => ({ waitLock() {}, releaseLock() {} }) },
    Utilities: {
      formatDate, getUuid: randomUUID, DigestAlgorithm: { SHA_256: 'sha256' },
      computeDigest: (_, bytes) => signed(createHash('sha256').update(unsigned(bytes)).digest()),
      base64Decode: (s) => signed(Buffer.from(s, 'base64')),
      newBlob: (bytes, mime, name) => ({ bytes: unsigned(bytes), mime, name }),
    },
    MailApp: {
      sendEmail: (m) => { if (mailFails-- > 0) throw new Error('Service invoked too many times'); sent.push(m); },
      getRemainingDailyQuota: () => 1500,
    },
    ContentService: { MimeType: { JSON: 'json' }, createTextOutput: (s) => ({ body: JSON.parse(s), setMimeType() { return this; } }) },
  };
  vm.createContext(ctx);
  vm.runInContext(CODE, ctx);
  const post = (fields) => ctx.doPost({ postData: { contents: JSON.stringify(fields) } }).body;
  const send = (name = PDF, fields = {}, bytes = FILES[name]) => post({
    token: TOKEN, action: 'send', file_name: name, content_b64: bytes.toString('base64'), sha256: sha(bytes), ...fields });
  return { gs: ctx, sent, logs, script, user, post, send };
}

const LIVE = { DELIVERY_MODE: 'live', ALLOWED_DOMAINS: 'example.in', RECIPIENTS: 'lead1@example.in, lead2@example.in',
               TEST_RECIPIENT: 'owner@example.in', UPLOAD_TOKEN: TOKEN };
const TEST = { ...LIVE, DELIVERY_MODE: 'test' };

test('nothing is emailed until delivery is switched on', () => {
  const run = load({ ALLOWED_DOMAINS: 'example.in', RECIPIENTS: 'a@example.in', UPLOAD_TOKEN: TOKEN });
  assert.deepEqual([run.send().ok, run.send().status], [true, 'off']);
  assert.deepEqual(run.post({ token: TOKEN, action: 'check' }), { ok: true, status: 'off', mode: 'off', recipients: 0 });
  assert.equal(run.sent.length, 0);
});

test('unsafe settings stop delivery with a plain reason', () => {
  const cases = [
    [{ ...LIVE, DELIVERY_MODE: 'on' }, /must be off, test or live/],
    [{ ...LIVE, ALLOWED_DOMAINS: '' }, /ALLOWED_DOMAINS is empty/],
    [{ ...LIVE, RECIPIENTS: 'lead1@example.in, someone@gmail.com' }, /someone@gmail.com is outside ALLOWED_DOMAINS/],
    [{ ...LIVE, RECIPIENTS: 'not-an-address' }, /is not an email address/],
    [{ ...LIVE, RECIPIENTS: '' }, /RECIPIENTS is empty/],
    [{ ...TEST, TEST_RECIPIENT: '' }, /TEST_RECIPIENT is empty/],
    [{ ...TEST, TEST_RECIPIENT: 'me@other.com' }, /outside ALLOWED_DOMAINS/],
    [{ ...LIVE, MAX_SENDS_PER_DAY: '0' }, /MAX_SENDS_PER_DAY/],
  ];
  for (const [settings, err] of cases) {
    const run = load(settings);
    for (const r of [run.send(), run.post({ token: TOKEN, action: 'check' })]) {
      assert.deepEqual([r.ok, r.status], [false, 'error']);
      assert.match(r.detail, err);
    }
    assert.equal(run.sent.length, 0);
  }
});

test('check reports mode and recipient count, and sends nothing', () => {
  assert.deepEqual(load(LIVE).post({ token: TOKEN, action: 'check' }), { ok: true, status: 'ready', mode: 'live', recipients: 2 });
  const run = load(TEST);
  assert.deepEqual(run.post({ token: TOKEN, action: 'check' }), { ok: true, status: 'ready', mode: 'test', recipients: 1 });
  assert.equal(run.sent.length, 0);
});

test('test mode emails only the test recipient, once', () => {
  const run = load(TEST);
  assert.equal(run.send().status, 'sent');
  const again = run.send();
  assert.deepEqual([again.ok, again.status], [true, 'duplicate']);
  assert.match(again.detail, /already emailed at \d{4}-\d\d-\d\d \d\d:\d\d IST \(test mode\)/);
  assert.equal(run.sent.length, 1);
  assert.equal(run.sent[0].to, 'owner@example.in');
  assert.equal(run.sent[0].subject, `[TEST] Report: ${PDF}`);
  assert.match(run.sent[0].body, /only you received this.*2 approved recipient/);
});

test('PDF, Excel and CSV go out with the right type to the approved list only', () => {
  const run = load(LIVE);
  for (const name of Object.keys(FILES)) assert.equal(run.send(name).status, 'sent');
  assert.deepEqual(run.sent.map((m) => m.attachments[0].mime), [
    'application/pdf', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'text/csv']);
  assert.ok(run.sent.every((m) => m.to === 'lead1@example.in,lead2@example.in'));
  assert.deepEqual(unsigned([...run.sent[2].attachments[0].bytes]), FILES['callers.csv']);
});

test('a request can never add recipients', () => {
  const run = load(LIVE);
  assert.equal(run.send(PDF, { to: 'outsider@gmail.com', recipients: ['outsider@gmail.com'] }).status, 'sent');
  assert.equal(run.sent[0].to, 'lead1@example.in,lead2@example.in');
});

test('same content is a duplicate even under another name; changed content or resend goes out', () => {
  const run = load(LIVE);
  run.send();
  assert.equal(run.send('copy.pdf', {}, FILES[PDF]).status, 'duplicate');
  assert.equal(run.send(PDF, {}, Buffer.from('%PDF-1.7\ncorrected report')).status, 'sent');
  assert.equal(run.send(PDF, { resend: 'yes' }).status, 'duplicate');
  assert.equal(run.send(PDF, { resend: true }).status, 'sent');
  assert.equal(run.sent.length, 3);
});

test('a test send does not count as a live send', () => {
  const run = load(TEST);
  run.send();
  run.script.setProperty('DELIVERY_MODE', 'live');
  assert.equal(run.send().status, 'sent');
  assert.deepEqual(run.sent.map((m) => m.to), ['owner@example.in', 'lead1@example.in,lead2@example.in']);
});

test('misnamed, mismatched, empty or tampered files are never emailed', () => {
  const run = load(LIVE);
  const reject = (name, fields, bytes) => {
    const r = run.send(name, fields, bytes);
    assert.deepEqual([r.ok, r.status], [false, 'rejected'], `${name}: ${r.detail}`);
  };
  reject('../secrets.pdf', {}, FILES[PDF]);
  reject('report.docx', {}, FILES[PDF]);
  reject('.env.csv', {}, FILES['callers.csv']);
  reject('report.pdf', {}, Buffer.from('<html>not a pdf'));
  reject('plan.xlsx', {}, FILES[PDF]);
  reject('data.csv', {}, Buffer.from([0x50, 0x4b, 0x03, 0x04, 0x00, 0x01]));
  reject('empty.csv', {}, Buffer.alloc(0));
  reject(PDF, { sha256: 'ab'.repeat(32) });
  reject(PDF, { sha256: undefined });
  assert.equal(run.sent.length, 0);
});

test('daily cap stops runaway sending', () => {
  const run = load({ ...LIVE, MAX_SENDS_PER_DAY: '2' });
  run.user.setProperty('count:live:2020-01-01', '9');
  assert.equal(run.send(PDF).status, 'sent');
  assert.equal(run.send('callers.csv').status, 'sent');
  const r = run.send('call_plan_Elite_2026-10-12.xlsx');
  assert.deepEqual([r.ok, r.status], [false, 'limit']);
  assert.equal(run.sent.length, 2);
  assert.equal(run.user.getProperty('count:live:2020-01-01'), null);
});

test('a failed send leaves no mark, so the next try sends it', () => {
  const run = load(LIVE, { mailFails: 1 });
  const r = run.send();
  assert.deepEqual([r.ok, r.status], [false, 'error']);
  assert.match(r.detail, /too many times/);
  assert.deepEqual(run.user.data, {});
  assert.equal(run.send().status, 'sent');
  assert.equal(run.sent.length, 1);
});

test('a send cut off midway is never repeated', () => {
  const run = load(LIVE);
  run.user.setProperty(`sent:live:${sha(FILES[PDF])}`, JSON.stringify({ state: 'sending', at: '2026-10-12 09:00 IST', name: PDF }));
  const r = run.send();
  assert.equal(r.status, 'duplicate');
  assert.match(r.detail, /a send of this file started at/);
  assert.equal(run.sent.length, 0);
});

test('token and request shape are checked before anything else', () => {
  const run = load(LIVE);
  assert.deepEqual(run.send(PDF, { token: 'wrong' }), { ok: false, status: 'unauthorized' });
  assert.deepEqual(run.post({ token: 'wrong', action: 'check' }), { ok: false, status: 'unauthorized' });
  for (const contents of ['not json', 'null', '"text"']) {
    assert.equal(run.gs.doPost({ postData: { contents } }).body.status, 'bad_request');
  }
  assert.equal(run.gs.doPost({}).body.status, 'bad_request');
  assert.equal(run.post({ token: TOKEN, action: 'delete' }).status, 'bad_request');
  assert.equal(load({ ...LIVE, UPLOAD_TOKEN: 'short' }).send(PDF, { token: 'short' }).status, 'not_ready');
  assert.equal(run.sent.length, 0);
});

test('editor helpers: token never replaced, setup check and sent log send nothing and hide the token', () => {
  const run = load({ ...TEST, UPLOAD_TOKEN: '' });
  run.gs.createUploadToken();
  const t = run.script.getProperty('UPLOAD_TOKEN');
  assert.match(t, /^[0-9a-f]{64}$/);
  run.gs.createUploadToken();
  assert.equal(run.script.getProperty('UPLOAD_TOKEN'), t);
  run.gs.checkSetup();
  assert.match(run.logs.at(-1), /Mode: TEST \(only owner@example.in is emailed\)/);
  assert.match(run.logs.at(-1), /Upload token: set/);
  assert.doesNotMatch(run.logs.at(-1), new RegExp(t));
  run.gs.showSentLog();
  assert.equal(run.logs.at(-1), 'Nothing has been emailed yet.');
  assert.equal(run.sent.length, 0);
});
