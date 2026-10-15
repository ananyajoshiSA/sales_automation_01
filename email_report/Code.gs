/**
 * On-request report mailer (docs/email_report.md).
 *
 * Emails one completed report (PDF, Excel or CSV) to the approved recipients when the /email-report skill
 * posts it here, which happens only when someone asks "email this report". Nothing here runs on a timer.
 *
 * Every setting lives in Script Properties (Project Settings), never in this file:
 *   DELIVERY_MODE      off (default) | test | live
 *   ALLOWED_DOMAINS    comma-separated, e.g. lawsikho.in; every recipient must be in one of these
 *   RECIPIENTS         comma-separated approved addresses, used in live mode
 *   TEST_RECIPIENT     one address, the only one emailed in test mode
 *   UPLOAD_TOKEN       shared secret (createUploadToken() makes one)
 *   MAX_SENDS_PER_DAY  optional, default 20: a cap that limits the damage if the token ever leaks
 *
 * A request can never choose who gets an email. The sent log (User Properties) keeps one entry per mode
 * and file content, so the same file is never emailed twice unless the request says resend.
 */

var TYPES = {
  pdf: {mime: 'application/pdf', magic: '%PDF-', label: 'PDF'},
  xlsx: {mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', magic: 'PK\u0003\u0004', label: 'Excel'},
  csv: {mime: 'text/csv', magic: null, label: 'CSV'}
};
var FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,150}\.(pdf|xlsx|csv)$/i;
var TZ = 'Asia/Kolkata';
var MODES = ['off', 'test', 'live'];
var MAX_BYTES = 20 * 1024 * 1024;  // Gmail's attachment cap is 25 MB; stay clear of it
var MAX_RECIPIENTS = 50;
var EMAIL = /^[^\s@,;<>"]+@([A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+)$/;

// ------------------------------------------------------------------ settings

function list_(s) {
  return String(s || '').split(/[,;\s]+/).map(function (x) { return x.trim(); }).filter(String);
}

/** Read and check every setting. Throws a plain-language error on anything unsafe (fail closed). */
function readConfig_() {
  var p = PropertiesService.getScriptProperties().getProperties();
  var mode = String(p.DELIVERY_MODE || 'off').trim().toLowerCase();
  if (MODES.indexOf(mode) < 0) throw new Error('DELIVERY_MODE must be off, test or live, not "' + p.DELIVERY_MODE + '"');
  var cfg = {mode: mode, domains: list_(p.ALLOWED_DOMAINS).map(function (d) { return d.toLowerCase(); }),
             recipients: list_(p.RECIPIENTS), testRecipient: String(p.TEST_RECIPIENT || '').trim(), maxPerDay: 20};
  if (p.MAX_SENDS_PER_DAY) {
    cfg.maxPerDay = Number(p.MAX_SENDS_PER_DAY);
    if (!(cfg.maxPerDay >= 1 && cfg.maxPerDay <= 100 && cfg.maxPerDay % 1 === 0)) {
      throw new Error('MAX_SENDS_PER_DAY must be a whole number from 1 to 100');
    }
  }
  if (mode === 'off') return cfg;
  if (!cfg.domains.length) throw new Error('ALLOWED_DOMAINS is empty: add your company domain, e.g. lawsikho.in');
  var check = function (addr, setting) {
    var m = EMAIL.exec(addr);
    if (!m) throw new Error(setting + ': "' + addr + '" is not an email address');
    if (cfg.domains.indexOf(m[1].toLowerCase()) < 0) {
      throw new Error(setting + ': ' + addr + ' is outside ALLOWED_DOMAINS (' + cfg.domains.join(', ') + ')');
    }
  };
  if (mode === 'test') {
    if (!cfg.testRecipient) throw new Error('TEST_RECIPIENT is empty: test mode emails only that one address');
    check(cfg.testRecipient, 'TEST_RECIPIENT');
  } else {
    if (!cfg.recipients.length) throw new Error('RECIPIENTS is empty: live mode needs the approved addresses');
    if (cfg.recipients.length > MAX_RECIPIENTS) throw new Error('RECIPIENTS has more than ' + MAX_RECIPIENTS + ' addresses');
    cfg.recipients.forEach(function (a) { check(a, 'RECIPIENTS'); });
  }
  return cfg;
}

function recipientsFor_(cfg) {
  return cfg.mode === 'live' ? cfg.recipients : cfg.mode === 'test' ? [cfg.testRecipient] : [];
}

function token_() {
  var t = String(PropertiesService.getScriptProperties().getProperty('UPLOAD_TOKEN') || '');
  return t.length >= 32 ? t : '';
}

function sameSecret_(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  var diff = 0;
  for (var i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

// ------------------------------------------------------------------ sending

function nowIst_(fmt) {
  return Utilities.formatDate(new Date(), TZ, fmt);
}

function hex_(bytes) {
  return bytes.map(function (b) { return ((b + 256) % 256).toString(16).replace(/^(.)$/, '0$1'); }).join('');
}

/** The file's type from its name, checked against its first bytes; null if it is not a PDF, Excel or CSV file. */
function typeOf_(name, bytes) {
  var m = FILE_NAME.exec(name || '');
  if (!m) return null;
  var t = TYPES[m[1].toLowerCase()];
  if (t.magic) return String.fromCharCode.apply(null, bytes.slice(0, t.magic.length)) === t.magic ? t : null;
  return bytes.slice(0, 4096).indexOf(0) < 0 ? t : null;  // a CSV is text: no zero bytes
}

function message_(name, type, mode, liveCount) {
  var lines = [
    'Hello,',
    '',
    'The report ' + name + ' (' + type.label + ') is attached.',
    'It was sent on request at ' + nowIst_('d MMM yyyy, h:mm a') + ' IST. The report itself says how fresh its data is.',
    '',
    'Please do not forward it outside the company: it may name callers and leads.'
  ];
  if (mode === 'test') {
    lines.unshift('TEST EMAIL: only you received this. In live mode it goes to ' + liveCount + ' approved recipient(s).', '');
  }
  return {subject: (mode === 'test' ? '[TEST] ' : '') + 'Report: ' + name, body: lines.join('\n')};
}

/**
 * Email one file at most once per mode. Returns {status, detail}; status is one of
 * sent, duplicate, off, limit, rejected. Throws only when sending itself failed (safe to retry).
 */
function deliver_(name, bytes, opts) {
  opts = opts || {};
  var cfg = readConfig_();
  if (!FILE_NAME.test(name || '')) {
    return {status: 'rejected', detail: 'file name must end in .pdf, .xlsx or .csv and use only letters, digits, spaces and . _ ( ) + -'};
  }
  if (cfg.mode === 'off') return {status: 'off', detail: 'DELIVERY_MODE is off, so nothing was emailed'};
  if (!bytes.length) return {status: 'rejected', detail: 'file is empty'};
  if (bytes.length > MAX_BYTES) return {status: 'rejected', detail: 'file is over 20 MB, too large to email'};
  var type = typeOf_(name, bytes);
  if (!type) return {status: 'rejected', detail: 'file content does not match its .' + name.split('.').pop() + ' name'};
  var sha = hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes));
  if (String(opts.sha256 || '').toLowerCase() !== sha) {
    return {status: 'rejected', detail: 'checksum mismatch: the upload arrived incomplete, nothing was emailed'};
  }

  var key = 'sent:' + cfg.mode + ':' + sha;
  var today = 'count:' + cfg.mode + ':' + nowIst_('yyyy-MM-dd');
  var ledger = PropertiesService.getUserProperties();
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var prev = ledger.getProperty(key);
    if (prev && !opts.resend) {
      var p = JSON.parse(prev);
      return {status: 'duplicate', detail: (p.state === 'sent' ? 'this exact file was already emailed at ' : 'a send of this file started at ') +
              p.at + ' (' + cfg.mode + ' mode); not emailed again'};
    }
    var count = Number(ledger.getProperty(today) || 0);
    if (count >= cfg.maxPerDay) {
      return {status: 'limit', detail: 'already ' + count + ' emails today, the MAX_SENDS_PER_DAY limit; nothing was emailed'};
    }
    var to = recipientsFor_(cfg);
    var at = nowIst_('yyyy-MM-dd HH:mm') + ' IST';
    // Mark before sending: if the script is killed mid-send, the file is never emailed twice.
    ledger.setProperty(key, JSON.stringify({state: 'sending', at: at, name: name}));
    var msg = message_(name, type, cfg.mode, cfg.recipients.length);
    try {
      MailApp.sendEmail({to: to.join(','), subject: msg.subject, body: msg.body, name: 'Sales reports',
                         attachments: [Utilities.newBlob(bytes, type.mime, name)]});
    } catch (e) {
      ledger.deleteProperty(key);  // nothing went out, so a later try may send it
      throw e;
    }
    ledger.setProperty(key, JSON.stringify({state: 'sent', at: at, name: name, to: to.length, resend: !!opts.resend}));
    Object.keys(ledger.getProperties()).forEach(function (k) {
      if (k.indexOf('count:') === 0 && k !== today) ledger.deleteProperty(k);  // only today's count matters
    });
    ledger.setProperty(today, String(count + 1));
    return {status: 'sent', detail: 'emailed to ' + to.length + ' recipient(s) in ' + cfg.mode + ' mode'};
  } finally {
    lock.releaseLock();
  }
}

// ------------------------------------------------------------------ web app

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/** action "check": what would happen, sends nothing. action "send": email one file. */
function doPost(e) {
  var req;
  try {
    req = JSON.parse(e && e.postData && e.postData.contents || '');
  } catch (err) {
    req = null;
  }
  if (!req || typeof req !== 'object') return reply_({ok: false, status: 'bad_request', detail: 'body is not a JSON object'});
  var expected = token_();
  if (!expected) return reply_({ok: false, status: 'not_ready', detail: 'UPLOAD_TOKEN is not set up'});
  if (!sameSecret_(String(req.token || ''), expected)) return reply_({ok: false, status: 'unauthorized'});
  try {
    if (req.action === 'check') {
      var cfg = readConfig_();
      return reply_({ok: true, status: cfg.mode === 'off' ? 'off' : 'ready', mode: cfg.mode, recipients: recipientsFor_(cfg).length});
    }
    if (req.action !== 'send') return reply_({ok: false, status: 'bad_request', detail: 'action must be check or send'});
    var name = String(req.file_name || '');
    var r = deliver_(name, Utilities.base64Decode(String(req.content_b64 || '')),
                     {sha256: req.sha256, resend: req.resend === true});
    console.log('send: ' + name + ' -> ' + r.status + ': ' + r.detail);
    r.ok = ['sent', 'duplicate', 'off'].indexOf(r.status) >= 0;
    return reply_(r);
  } catch (err) {
    console.error('request failed: ' + err);
    return reply_({ok: false, status: 'error', detail: String(err && err.message || err)});
  }
}

// ------------------------------------------------------------------ run these from the editor

/** Makes the shared secret once and shows it in the log, to copy into REPORT_EMAIL_TOKEN. */
function createUploadToken() {
  if (token_()) {
    console.log('UPLOAD_TOKEN already exists. To replace it, delete it in Project Settings and run this again.');
    return;
  }
  var t = (Utilities.getUuid() + Utilities.getUuid()).replace(/-/g, '');
  PropertiesService.getScriptProperties().setProperty('UPLOAD_TOKEN', t);
  console.log('New UPLOAD_TOKEN (copy it into REPORT_EMAIL_TOKEN, keep it private):\n' + t);
}

/** Checks every setting and says, in plain words, what would happen now. Sends nothing. */
function checkSetup() {
  var cfg = readConfig_();
  console.log([
    'Mode: ' + cfg.mode.toUpperCase() + {off: ' (nothing is emailed)', test: ' (only ' + cfg.testRecipient + ' is emailed)',
                                         live: ' (approved recipients are emailed)'}[cfg.mode],
    'Allowed domains: ' + (cfg.domains.join(', ') || '(none yet)'),
    'Approved recipients (' + cfg.recipients.length + '): ' + (cfg.recipients.join(', ') || '(none yet)'),
    'Test recipient: ' + (cfg.testRecipient || '(none yet)'),
    'Upload token: ' + (token_() ? 'set' : 'MISSING: run createUploadToken'),
    'At most ' + cfg.maxPerDay + ' emails a day',
    'Emails left today (Google quota): ' + MailApp.getRemainingDailyQuota()
  ].join('\n'));
}

/** Lists every file this script has emailed, newest first. */
function showSentLog() {
  var all = PropertiesService.getUserProperties().getProperties();
  var rows = Object.keys(all).filter(function (k) { return k.indexOf('sent:') === 0; }).map(function (k) {
    var v = JSON.parse(all[k]);
    return v.at + '  ' + k.split(':')[1] + '  ' + v.name + '  ' + v.state + (v.to ? ' to ' + v.to : '') + (v.resend ? ' (resend)' : '');
  }).sort().reverse();
  console.log(rows.length ? rows.join('\n') : 'Nothing has been emailed yet.');
}
