/**
 * On-request report mailer (docs/email_report.md).
 *
 * Emails a set of completed reports (PDF, Excel, CSV, Word, PowerPoint, PNG/JPEG images, HTML), all in one email,
 * to the approved recipients when the /email-report skill posts them here, which happens only when someone asks
 * "email this report". Nothing here runs on a timer.
 *
 * Every setting lives in Script Properties (Project Settings), never in this file:
 *   DELIVERY_MODE      off (default) | test | live
 *   ALLOWED_DOMAINS    comma-separated, e.g. lawsikho.in; every recipient must be in one of these
 *   RECIPIENTS         comma-separated approved addresses, used in live mode when a request names no list
 *   LIST_<NAME>        optional named lists, e.g. LIST_ELITE: comma-separated approved addresses, used in live mode
 *                      when a request names that list ("elite"); an unknown name sends nothing
 *   TEST_RECIPIENT     one address, the only one emailed in test mode
 *   UPLOAD_TOKEN       shared secret (createUploadToken() makes one)
 *   MAX_SENDS_PER_DAY  optional, default 20: a cap that limits the damage if the token ever leaks
 *
 * A request can never choose who gets an email: at most it names one of these lists. The sent log (User Properties) keeps one entry per mode
 * and file content, so the same file is never emailed twice unless the request says resend. One email, however
 * many files it carries, counts once toward MAX_SENDS_PER_DAY.
 */

var ZIP = [0x50, 0x4b, 0x03, 0x04];
var TYPES = {
  pdf: {mime: 'application/pdf', magic: [0x25, 0x50, 0x44, 0x46, 0x2d], label: 'PDF'},
  xlsx: {mime: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', magic: ZIP, label: 'Excel'},
  xls: {mime: 'application/vnd.ms-excel', magic: [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1], label: 'Excel'},
  csv: {mime: 'text/csv', magic: null, label: 'CSV'},
  docx: {mime: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', magic: ZIP, label: 'Word'},
  pptx: {mime: 'application/vnd.openxmlformats-officedocument.presentationml.presentation', magic: ZIP, label: 'PowerPoint'},
  png: {mime: 'image/png', magic: [0x89, 0x50, 0x4e, 0x47], label: 'PNG image'},
  jpg: {mime: 'image/jpeg', magic: [0xff, 0xd8, 0xff], label: 'JPEG image'},
  jpeg: {mime: 'image/jpeg', magic: [0xff, 0xd8, 0xff], label: 'JPEG image'},
  html: {mime: 'text/html', magic: null, label: 'HTML'}
};
var FILE_NAME = /^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,150}\.(pdf|xlsx|xls|csv|docx|pptx|png|jpe?g|html)$/i;
var NAME_RULE = 'file name must end in .pdf, .xlsx, .xls, .csv, .docx, .pptx, .png, .jpg or .html and use only letters, digits, spaces and . _ ( ) + -';
var TZ = 'Asia/Kolkata';
var MODES = ['off', 'test', 'live'];
var MAX_BYTES = 20 * 1024 * 1024;  // Gmail's attachment cap is 25 MB per email; stay clear of it
var MAX_FILES = 10;
var MAX_RECIPIENTS = 50;
var EMAIL = /^[^\s@,;<>"]+@([A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+)$/;
var LIST_KEY = /^LIST_([A-Z0-9_]{1,40})$/;
var LIST_NAME = /^[a-z0-9_]{1,40}$/;

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
             recipients: list_(p.RECIPIENTS), testRecipient: String(p.TEST_RECIPIENT || '').trim(), maxPerDay: 20, lists: {}};
  Object.keys(p).forEach(function (k) {
    var m = LIST_KEY.exec(k);
    if (m) cfg.lists[m[1].toLowerCase()] = list_(p[k]);
  });
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
    Object.keys(cfg.lists).forEach(function (name) {
      var key = 'LIST_' + name.toUpperCase();
      if (!cfg.lists[name].length) throw new Error(key + ' is empty: add its addresses or delete it');
      if (cfg.lists[name].length > MAX_RECIPIENTS) throw new Error(key + ' has more than ' + MAX_RECIPIENTS + ' addresses');
      cfg.lists[name].forEach(function (a) { check(a, key); });
    });
  }
  return cfg;
}

/** The list a request named: '' for none (RECIPIENTS), the lower-case name, or null when it is not a list name. */
function listName_(raw) {
  if (raw === undefined || raw === null || raw === '') return '';
  var name = String(raw).trim().toLowerCase();
  return LIST_NAME.test(name) ? name : null;
}

/** Why the named list can't be used, or '' if it can. */
function listProblem_(cfg, name) {
  if (name === null) return 'the list name must use only letters, digits and _';
  if (name && !cfg.lists[name]) {
    return 'there is no LIST_' + name.toUpperCase() + ' in Script Properties (lists: ' +
           (Object.keys(cfg.lists).sort().join(', ') || 'none') + ')';
  }
  return '';
}

function liveRecipients_(cfg, name) {
  return name ? cfg.lists[name] : cfg.recipients;
}

function recipientsFor_(cfg, name) {
  return cfg.mode === 'live' ? liveRecipients_(cfg, name) : cfg.mode === 'test' ? [cfg.testRecipient] : [];
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

/** The file's type from its name, checked against its first bytes; null if it is not an allowed report type. */
function typeOf_(name, bytes) {
  var m = FILE_NAME.exec(name || '');
  if (!m) return null;
  var t = TYPES[m[1].toLowerCase()];
  if (t.magic) {
    return t.magic.every(function (b, i) { return i < bytes.length && (bytes[i] + 256) % 256 === b; }) ? t : null;
  }
  return bytes.slice(0, 4096).indexOf(0) < 0 ? t : null;  // CSV and HTML are text: no zero bytes
}

function message_(files, mode, liveCount) {
  var one = files.length === 1;
  var lines = ['Hello,', ''].concat(one ? ['The report ' + files[0].name + ' (' + files[0].type.label + ') is attached.'] :
    ['These ' + files.length + ' reports are attached:'].concat(files.map(function (f) { return '- ' + f.name + ' (' + f.type.label + ')'; })), [
    '',
    'Sent on request at ' + nowIst_('d MMM yyyy, h:mm a') + ' IST. Each report says how fresh its data is.',
    '',
    'Please do not forward ' + (one ? 'it' : 'them') + ' outside the company: ' + (one ? 'it' : 'they') + ' may name callers and leads.'
  ]);
  if (mode === 'test') {
    lines.unshift('TEST EMAIL: only you received this. In live mode it goes to ' + liveCount + ' approved recipient(s).', '');
  }
  var subject = one ? 'Report: ' + files[0].name : 'Reports: ' + files.length + ' files, ' + nowIst_('d MMM yyyy');
  return {subject: (mode === 'test' ? '[TEST] ' : '') + subject, body: lines.join('\n')};
}

/** Checks one uploaded file; returns {name, bytes, type, sha} or {rejected: reason}. */
function checkFile_(f) {
  var name = String(f.name || '');
  if (!FILE_NAME.test(name)) return {rejected: name + ': ' + NAME_RULE};
  if (!f.bytes.length) return {rejected: name + ': file is empty'};
  var type = typeOf_(name, f.bytes);
  if (!type) return {rejected: name + ': file content does not match its .' + name.split('.').pop() + ' name'};
  var sha = hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, f.bytes));
  if (String(f.sha256 || '').toLowerCase() !== sha) {
    return {rejected: name + ': checksum mismatch, the upload arrived incomplete'};
  }
  return {name: name, bytes: f.bytes, type: type, sha: sha};
}

/**
 * Email a set of files together, each file at most once per mode: files already emailed are left out of the
 * email (all of them already sent answers duplicate) unless the request says resend. Returns {status, detail};
 * status is one of sent, duplicate, off, limit, rejected. Throws only when sending itself failed (safe to retry).
 */
function deliver_(uploads, opts) {
  opts = opts || {};
  var cfg = readConfig_();
  if (!uploads.length) return {status: 'rejected', detail: 'no file was uploaded'};
  if (uploads.length > MAX_FILES) return {status: 'rejected', detail: 'more than ' + MAX_FILES + ' files in one email'};
  var bad = uploads.filter(function (f) { return !FILE_NAME.test(String(f.name || '')); })[0];
  if (bad) return {status: 'rejected', detail: (uploads.length > 1 ? String(bad.name || '(no name)') + ': ' : '') + NAME_RULE};
  if (cfg.mode === 'off') return {status: 'off', detail: 'DELIVERY_MODE is off, so nothing was emailed'};
  var listName = listName_(opts.list);
  var listBad = listProblem_(cfg, listName);
  if (listBad) return {status: 'rejected', detail: listBad + '; nothing was emailed'};
  var total = uploads.reduce(function (n, f) { return n + f.bytes.length; }, 0);
  if (total > MAX_BYTES) return {status: 'rejected', detail: (uploads.length > 1 ? 'files are' : 'file is') + ' over 20 MB, too large to email'};
  var files = [];
  for (var i = 0; i < uploads.length; i++) {
    var c = checkFile_(uploads[i]);
    if (c.rejected) {
      return {status: 'rejected', detail: (uploads.length > 1 ? c.rejected : c.rejected.replace(/^[^:]*: /, '')) + '; nothing was emailed'};
    }
    if (files.some(function (f) { return f.name === c.name; })) {
      return {status: 'rejected', detail: 'two files are both named ' + c.name + '; nothing was emailed'};
    }
    if (!files.some(function (f) { return f.sha === c.sha; })) files.push(c);
  }

  var today = 'count:' + cfg.mode + ':' + nowIst_('yyyy-MM-dd');
  var ledger = PropertiesService.getUserProperties();
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    // Each list keeps its own log, so a report emailed to one team can still go to another.
    var keyOf = function (f) { return 'sent:' + cfg.mode + ':' + (listName ? listName + ':' : '') + f.sha; };
    var before = files.filter(function (f) { return ledger.getProperty(keyOf(f)); });
    var send = opts.resend ? files : files.filter(function (f) { return !ledger.getProperty(keyOf(f)); });
    if (!send.length) {
      var p = JSON.parse(ledger.getProperty(keyOf(files[0])));
      var what = files.length === 1 ? 'this exact file was already emailed at ' : 'all ' + files.length + ' files were already emailed, the first at ';
      return {status: 'duplicate', detail: (p.state === 'sent' ? what : 'a send of this file started at ') +
              p.at + ' (' + cfg.mode + ' mode); not emailed again'};
    }
    var count = Number(ledger.getProperty(today) || 0);
    if (count >= cfg.maxPerDay) {
      return {status: 'limit', detail: 'already ' + count + ' emails today, the MAX_SENDS_PER_DAY limit; nothing was emailed'};
    }
    var to = recipientsFor_(cfg, listName);
    var at = nowIst_('yyyy-MM-dd HH:mm') + ' IST';
    // Mark before sending: if the script is killed mid-send, the files are never emailed twice.
    send.forEach(function (f) { ledger.setProperty(keyOf(f), JSON.stringify({state: 'sending', at: at, name: f.name})); });
    var msg = message_(send, cfg.mode, liveRecipients_(cfg, listName).length);
    try {
      MailApp.sendEmail({to: to.join(','), subject: msg.subject, body: msg.body, name: 'Sales reports',
                         attachments: send.map(function (f) { return Utilities.newBlob(f.bytes, f.type.mime, f.name); })});
    } catch (e) {
      send.forEach(function (f) { ledger.deleteProperty(keyOf(f)); });  // nothing went out, so a later try may send them
      throw e;
    }
    send.forEach(function (f) {
      ledger.setProperty(keyOf(f), JSON.stringify({state: 'sent', at: at, name: f.name, to: to.length, resend: !!opts.resend}));
    });
    Object.keys(ledger.getProperties()).forEach(function (k) {
      if (k.indexOf('count:') === 0 && k !== today) ledger.deleteProperty(k);  // only today's count matters
    });
    ledger.setProperty(today, String(count + 1));
    var skipped = opts.resend ? [] : before;
    return {status: 'sent', detail: (send.length === 1 ? '' : send.length + ' files in one email, ') + 'emailed to ' + to.length +
            ' recipient(s) in ' + cfg.mode + ' mode' + (listName ? ', list ' + listName : '') +
            (skipped.length ? '; left out, already emailed: ' + skipped.map(function (f) { return f.name; }).join(', ') : '')};
  } finally {
    lock.releaseLock();
  }
}

// ------------------------------------------------------------------ web app

function reply_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

/** action "check": what would happen, sends nothing. action "send": email one file (file_name, content_b64,
 * sha256) or several in one email (files: a list of those three). Either may name a recipient list (list). */
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
      var name = listName_(req.list);
      var bad = cfg.mode === 'off' ? '' : listProblem_(cfg, name);
      if (bad) return reply_({ok: false, status: 'unknown_list', detail: bad});
      return reply_({ok: true, status: cfg.mode === 'off' ? 'off' : 'ready', mode: cfg.mode, recipients: recipientsFor_(cfg, name).length,
                     multi: true, list: name || '', lists: Object.keys(cfg.lists).sort()});
    }
    if (req.action !== 'send') return reply_({ok: false, status: 'bad_request', detail: 'action must be check or send'});
    var list = Array.isArray(req.files) ? req.files : [req];
    var uploads = list.map(function (f) {
      f = f && typeof f === 'object' ? f : {};
      return {name: String(f.file_name || ''), bytes: Utilities.base64Decode(String(f.content_b64 || '')), sha256: f.sha256};
    });
    var r = deliver_(uploads, {resend: req.resend === true, list: req.list});
    console.log('send: ' + uploads.map(function (f) { return f.name; }).join(', ') + ' -> ' + r.status + ': ' + r.detail);
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
    'Approved recipients (' + cfg.recipients.length + '): ' + (cfg.recipients.join(', ') || '(none yet)')
  ].concat(Object.keys(cfg.lists).sort().map(function (n) {
    return 'List ' + n + ' (' + cfg.lists[n].length + '): ' + cfg.lists[n].join(', ');
  }), [
    'Test recipient: ' + (cfg.testRecipient || '(none yet)'),
    'Upload token: ' + (token_() ? 'set' : 'MISSING: run createUploadToken'),
    'At most ' + cfg.maxPerDay + ' emails a day',
    'Emails left today (Google quota): ' + MailApp.getRemainingDailyQuota()
  ]).join('\n'));
}

/** Lists every file this script has emailed, newest first. */
function showSentLog() {
  var all = PropertiesService.getUserProperties().getProperties();
  var rows = Object.keys(all).filter(function (k) { return k.indexOf('sent:') === 0; }).map(function (k) {
    var v = JSON.parse(all[k]);
    var parts = k.split(':');
    return v.at + '  ' + parts[1] + (parts.length > 3 ? ' ' + parts[2] : '') + '  ' + v.name + '  ' + v.state +
           (v.to ? ' to ' + v.to : '') + (v.resend ? ' (resend)' : '');
  }).sort().reverse();
  console.log(rows.length ? rows.join('\n') : 'Nothing has been emailed yet.');
}
