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
    'd MMM yyyy': `${day} ${MON[m]} ${y}`,
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
  assert.deepEqual(run.post({ token: TOKEN, action: 'check' }), { ok: true, status: 'off', mode: 'off', recipients: 0, multi: true, list: '', lists: [] });
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
    [{ ...LIVE, LIST_ELITE: 'lead3@example.in, someone@gmail.com' }, /LIST_ELITE: someone@gmail.com is outside ALLOWED_DOMAINS/],
    [{ ...LIVE, LIST_ELITE: ' ' }, /LIST_ELITE is empty/],
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
  assert.deepEqual(load(LIVE).post({ token: TOKEN, action: 'check' }), { ok: true, status: 'ready', mode: 'live', recipients: 2, multi: true, list: '', lists: [] });
  const run = load(TEST);
  assert.deepEqual(run.post({ token: TOKEN, action: 'check' }), { ok: true, status: 'ready', mode: 'test', recipients: 1, multi: true, list: '', lists: [] });
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

const many = (names, fields = {}, files = FILES) => names.map((n) => ({
  file_name: n, content_b64: files[n].toString('base64'), sha256: sha(files[n]), ...fields }));
const sendMany = (run, names, fields = {}, files = FILES) =>
  run.post({ token: TOKEN, action: 'send', files: many(names, {}, files), ...fields });

test('a set of files goes out together in one email and counts once', () => {
  const run = load(LIVE);
  const r = sendMany(run, Object.keys(FILES));
  assert.deepEqual([r.ok, r.status], [true, 'sent']);
  assert.match(r.detail, /^3 files in one email, emailed to 2 recipient/);
  assert.equal(run.sent.length, 1);
  assert.deepEqual([...run.sent[0].attachments.map((a) => a.name)], Object.keys(FILES));
  assert.match(run.sent[0].subject, /^Reports: 3 files, \d+ \w{3} \d{4}$/);
  assert.match(run.sent[0].body, /These 3 reports are attached:\n- team_calling_report_2026-10-11.pdf \(PDF\)/);
  assert.equal(Object.entries(run.user.data).filter(([k]) => k.startsWith('count:'))[0][1], '1');
});

test('files already emailed are left out of a set; a set all sent before is a duplicate unless resent', () => {
  const run = load(LIVE);
  run.send(PDF);
  const r = sendMany(run, Object.keys(FILES));
  assert.equal(r.status, 'sent');
  assert.match(r.detail, /left out, already emailed: team_calling_report_2026-10-11.pdf/);
  assert.deepEqual([...run.sent[1].attachments.map((a) => a.name)], ['call_plan_Elite_2026-10-12.xlsx', 'callers.csv']);
  const again = sendMany(run, Object.keys(FILES));
  assert.deepEqual([again.ok, again.status], [true, 'duplicate']);
  assert.match(again.detail, /all 3 files were already emailed/);
  assert.equal(sendMany(run, Object.keys(FILES), { resend: true }).status, 'sent');
  assert.equal(run.sent.at(-1).attachments.length, 3);
});

test('one bad file, a repeated name, too many files or too many bytes stop the whole set', () => {
  const run = load(LIVE);
  const files = { ...FILES, 'fake.pdf': Buffer.from('<html>') };
  let r = sendMany(run, [PDF, 'fake.pdf'], {}, files);
  assert.deepEqual([r.status, r.detail], ['rejected', 'fake.pdf: file content does not match its .pdf name; nothing was emailed']);
  const other = Buffer.from('%PDF-1.7\nanother report');
  r = run.post({ token: TOKEN, action: 'send', files: [...many([PDF]),
    { file_name: PDF, content_b64: other.toString('base64'), sha256: sha(other) }] });
  assert.match(r.detail, /both named team_calling_report_2026-10-11.pdf/);
  r = run.post({ token: TOKEN, action: 'send', files: Array(11).fill(many([PDF])[0]) });
  assert.match(r.detail, /more than 10 files/);
  run.gs.MAX_BYTES = 40;
  r = sendMany(run, Object.keys(FILES));
  assert.deepEqual([r.status, r.detail], ['rejected', 'files are over 20 MB, too large to email']);
  assert.equal(run.sent.length, 0);
});

test('Word, PowerPoint, old Excel, images and HTML go out with the right type; fakes do not', () => {
  const run = load(LIVE);
  const files = {
    'notes.docx': Buffer.from('PK\u0003\u0004 doc'), 'deck.pptx': Buffer.from('PK\u0003\u0004 deck'),
    'old.xls': Buffer.from([0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1, 1]), 'chart.png': Buffer.from([0x89, 0x50, 0x4e, 0x47, 13, 10]),
    'photo.jpg': Buffer.from([0xff, 0xd8, 0xff, 0xe0, 1]), 'page.html': Buffer.from('<!doctype html><p>hi</p>'),
  };
  assert.equal(sendMany(run, Object.keys(files), {}, files).status, 'sent');
  assert.deepEqual([...run.sent[0].attachments.map((a) => a.mime)], [
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'application/vnd.ms-excel', 'image/png', 'image/jpeg', 'text/html']);
  const fake = { 'chart.png': Buffer.from('%PDF-1.7') };
  assert.equal(sendMany(run, ['chart.png'], {}, fake).status, 'rejected');
  assert.equal(run.send('run.exe', {}, FILES[PDF]).status, 'rejected');
});

const LISTS = { ...LIVE, LIST_ELITE: 'lead1@example.in, tl@example.in', LIST_DEEPANSHI: 'lead2@example.in; dk@example.in' };

test('a named list sends to that list only; no list sends to RECIPIENTS', () => {
  const run = load(LISTS);
  assert.deepEqual(run.post({ token: TOKEN, action: 'check', list: 'Elite' }),
    { ok: true, status: 'ready', mode: 'live', recipients: 2, multi: true, list: 'elite', lists: ['deepanshi', 'elite'] });
  const r = run.send(PDF, { list: 'elite' });
  assert.equal(r.status, 'sent');
  assert.match(r.detail, /list elite/);
  assert.equal(run.send('callers.csv', { list: 'deepanshi' }).status, 'sent');
  assert.equal(run.send('call_plan_Elite_2026-10-12.xlsx').status, 'sent');
  assert.deepEqual(run.sent.map((m) => m.to),
    ['lead1@example.in,tl@example.in', 'lead2@example.in,dk@example.in', 'lead1@example.in,lead2@example.in']);
});

test('an unknown or malformed list sends nothing', () => {
  const run = load(LISTS);
  const c = run.post({ token: TOKEN, action: 'check', list: 'sana' });
  assert.deepEqual([c.ok, c.status], [false, 'unknown_list']);
  assert.match(c.detail, /no LIST_SANA .*deepanshi, elite/);
  for (const list of ['sana', 'a@b.in', '../x']) {
    const r = run.send(PDF, { list });
    assert.deepEqual([r.ok, r.status], [false, 'rejected']);
  }
  assert.equal(run.sent.length, 0);
});

test('each list keeps its own sent log; test mode still emails only the test address', () => {
  const run = load(LISTS);
  assert.equal(run.send(PDF, { list: 'elite' }).status, 'sent');
  assert.equal(run.send(PDF, { list: 'elite' }).status, 'duplicate');
  assert.equal(run.send(PDF, { list: 'deepanshi' }).status, 'sent');
  assert.equal(run.send(PDF).status, 'sent');
  assert.equal(run.sent.length, 3);
  const t = load({ ...LISTS, DELIVERY_MODE: 'test' });
  assert.equal(t.send(PDF, { list: 'elite' }).status, 'sent');
  assert.equal(t.sent[0].to, 'owner@example.in');
  assert.match(t.sent[0].body, /In live mode it goes to 2 approved recipient/);
  assert.equal(t.send(PDF, { list: 'sana' }).status, 'rejected');
});
