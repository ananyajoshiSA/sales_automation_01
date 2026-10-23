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
 * WhatsApp (WATI), optional, off until WHATSAPP_MODE is live. After a live email to list NAME, the same new files also
 * go on WhatsApp to WA_LIST_NAME, one message per file per number, using the approved WATI template, whose header
 * document link is {{1}} and body {{2}} report, {{3}} team, {{4}} date(s):
 *   WHATSAPP_MODE      off (default) | live
 *   WATI_TOKEN         the WATI API key;  WATI_CLIENT_ID  the WATI client (tenant) ID
 *   WA_LIST_<NAME>     comma-separated numbers with country code, e.g. 9198xxxxxxxx
 *   WA_TEST_NUMBER     one number, the only one sendWhatsAppTest() messages
 *   WATI_TEMPLATE      optional, default daily_report_pdf;  WATI_CHANNEL_NUMBER  optional sender number
 *   WA_MAX_PER_DAY     optional, default 200 messages a day
 * Each file is kept in the Drive folder "Sales reports/<team>/<date>" for good. WhatsApp collects it through a link
 * that is closed again after 3 days; people keep the file itself in their chat.
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
    var result = {status: 'sent', detail: (send.length === 1 ? '' : send.length + ' files in one email, ') + 'emailed to ' + to.length +
            ' recipient(s) in ' + cfg.mode + ' mode' + (listName ? ', list ' + listName : '') +
            (skipped.length ? '; left out, already emailed: ' + skipped.map(function (f) { return f.name; }).join(', ') : '')};
  } finally {
    lock.releaseLock();
  }
  // WhatsApp follows only a live email, and only with the files this email carried, so it is never sent twice either.
  if (cfg.mode === 'live' && listName) {
    var wa = whatsappAfterEmail_(send, listName);
    if (wa) result.detail += '; WhatsApp: ' + wa;
  }
  return result;
}

// ------------------------------------------------------------------ WhatsApp (WATI)

var WA_LIST_KEY = /^WA_LIST_([A-Z0-9_]{1,40})$/;
var WA_NUMBER = /^[1-9][0-9]{10,14}$/;
var WA_TENANT = /^[A-Za-z0-9_-]{1,64}$/;
var WA_TEMPLATE = /^[a-z0-9_]{1,512}$/;
var WA_LINK_DAYS = 3;
var WA_HOST = 'https://live-mt-server.wati.io';
var ARCHIVE = 'Sales reports';
var MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "919812345678" (a leading + is fine); null when it is not a number with country code. */
function waEntry_(raw) {
  var n = String(raw).trim().replace(/^\+/, '');
  return WA_NUMBER.test(n) ? {number: n} : null;
}

/** WhatsApp settings. Never throws: anything wrong comes back as problem, so email is never held up by WhatsApp. */
function readWhatsApp_() {
  var p = PropertiesService.getScriptProperties().getProperties();
  var w = {mode: String(p.WHATSAPP_MODE || 'off').trim().toLowerCase(), token: String(p.WATI_TOKEN || '').trim(),
           tenant: String(p.WATI_CLIENT_ID || '').trim(), template: String(p.WATI_TEMPLATE || 'daily_report_pdf').trim(),
           channel: String(p.WATI_CHANNEL_NUMBER || '').trim().replace(/^\+/, ''), maxPerDay: 200, lists: {}, test: null, problem: ''};
  var problems = [];
  if (['off', 'live'].indexOf(w.mode) < 0) problems.push('WHATSAPP_MODE must be off or live, not "' + p.WHATSAPP_MODE + '"');
  Object.keys(p).forEach(function (k) {
    var m = WA_LIST_KEY.exec(k);
    if (!m) return;
    var entries = list_(p[k]);
    var parsed = entries.map(waEntry_);
    var bad = entries.filter(function (e, i) { return !parsed[i]; });
    if (bad.length) problems.push(k + ': "' + bad[0] + '" is not a number with country code, e.g. 9198xxxxxxxx');
    else if (!parsed.length) problems.push(k + ' is empty: add its numbers or delete it');
    else if (parsed.length > MAX_RECIPIENTS) problems.push(k + ' has more than ' + MAX_RECIPIENTS + ' numbers');
    else w.lists[m[1].toLowerCase()] = parsed;
  });
  if (p.WA_TEST_NUMBER) {
    w.test = waEntry_(p.WA_TEST_NUMBER);
    if (!w.test) problems.push('WA_TEST_NUMBER is not a number with country code, e.g. 9198xxxxxxxx');
  }
  if (p.WA_MAX_PER_DAY) {
    w.maxPerDay = Number(p.WA_MAX_PER_DAY);
    if (!(w.maxPerDay >= 1 && w.maxPerDay <= 1000 && w.maxPerDay % 1 === 0)) problems.push('WA_MAX_PER_DAY must be a whole number from 1 to 1000');
  }
  if (!w.token) problems.push('WATI_TOKEN is empty');
  if (!WA_TENANT.test(w.tenant)) problems.push('WATI_CLIENT_ID is empty or has characters other than letters, digits, - and _');
  if (!WA_TEMPLATE.test(w.template)) problems.push('WATI_TEMPLATE must be a template name like daily_report_pdf');
  if (w.channel && !WA_NUMBER.test(w.channel)) problems.push('WATI_CHANNEL_NUMBER is not a number with country code');
  w.problem = problems.join('; ');
  return w;
}

function istDate_(y, m, d) {
  return d + ' ' + MONTHS[m - 1] + ' ' + y;
}

/** The template's words for a set of files: report name per file, team and the day or days the files cover. */
function waLabels_(file, listName) {
  var base = file.name.replace(/\.[^.]+$/, '');
  var dates = [];
  base = base.replace(/(\d{4})-(\d{2})-(\d{2})/g, function (all, y, m, d) {
    if (Number(m) >= 1 && Number(m) <= 12) dates.push([y, m, d].join('-'));
    return ' ';
  });
  var words = base.replace(/[_.+()-]+/g, ' ').replace(/\s+/g, ' ').trim();
  if (words.toLowerCase().indexOf(listName.replace(/_/g, ' ') + ' ') === 0) words = words.slice(listName.length + 1);
  words = words.replace(/\b(status) ([01]\d|2[0-3])([0-5]\d)\b/i, '$1 at $2:$3');
  words = (words.charAt(0).toUpperCase() + words.slice(1)) || 'Report';
  var team = listName.replace(/_/g, ' ');
  dates.sort();
  var period;
  if (!dates.length) {
    period = Utilities.formatDate(new Date(), TZ, 'd MMM yyyy');
  } else {
    var a = dates[0].split('-').map(Number), b = dates[dates.length - 1].split('-').map(Number);
    period = dates[0] === dates[dates.length - 1] ? istDate_(a[0], a[1], a[2]) :
      a[0] === b[0] && a[1] === b[1] ? a[2] + '–' + istDate_(b[0], b[1], b[2]) : istDate_(a[0], a[1], a[2]) + ' – ' + istDate_(b[0], b[1], b[2]);
  }
  team = team.charAt(0).toUpperCase() + team.slice(1) + (/\bteam$/i.test(team) ? '' : ' team');
  return {report: (words + ' (' + file.type.label + ')').slice(0, 60), team: team.slice(0, 60), period: period};
}

/** The archive folder for one team and day, made on first use; files are kept there for good. */
function archiveFolder_(team, day) {
  var store = PropertiesService.getUserProperties();
  var root = null, id = store.getProperty('wa:archive');
  if (id) {
    try { root = DriveApp.getFolderById(id); } catch (e) { root = null; }
  }
  if (!root) {
    root = DriveApp.createFolder(ARCHIVE);
    var closed = 'private';
    try {  // a Google Workspace account can open it to everyone at the company; a personal one keeps it private
      root.setSharing(DriveApp.Access.DOMAIN, DriveApp.Permission.VIEW);
      closed = 'domain';
    } catch (e) { /* keep private */ }
    store.setProperty('wa:archive', root.getId());
    store.setProperty('wa:closed', closed);
  }
  var sub = function (parent, name) {
    var it = parent.getFoldersByName(name);
    return it.hasNext() ? it.next() : parent.createFolder(name);
  };
  return sub(sub(root, team), day);
}

/** Closes every WhatsApp link opened more than WA_LINK_DAYS ago. The files stay in the archive. */
function closeExpiredLinks() {
  var store = PropertiesService.getUserProperties();
  var all = store.getProperties();
  var closed = all['wa:closed'] === 'domain' ? DriveApp.Access.DOMAIN : DriveApp.Access.PRIVATE;
  var cutoff = new Date().getTime() - WA_LINK_DAYS * 24 * 3600 * 1000;
  var n = 0;
  Object.keys(all).forEach(function (k) {
    if (k.indexOf('walink:') !== 0 || Number(all[k]) > cutoff) return;
    try {
      DriveApp.getFileById(k.slice(7)).setSharing(closed, DriveApp.Permission.VIEW);
    } catch (e) {
      console.error('could not close link ' + k.slice(7) + ': ' + e);
      return;  // tried again next time
    }
    store.deleteProperty(k);
    n++;
  });
  return n;
}

/** Makes sure closeExpiredLinks runs every 6 hours even on days nothing is sent. */
function ensureCleanupTrigger_() {
  try {
    var has = ScriptApp.getProjectTriggers().some(function (t) { return t.getHandlerFunction() === 'closeExpiredLinks'; });
    if (!has) ScriptApp.newTrigger('closeExpiredLinks').timeBased().everyHours(6).create();
  } catch (e) {
    console.error('could not set the link clean-up timer: ' + e);  // links still close on the next send
  }
}

/** Reserves n messages under today's cap; false when that would go over it. */
function waReserve_(w, n) {
  var store = PropertiesService.getUserProperties();
  var today = 'wacount:' + nowIst_('yyyy-MM-dd');
  var lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    var count = Number(store.getProperty(today) || 0);
    if (count + n > w.maxPerDay) return false;
    Object.keys(store.getProperties()).forEach(function (k) {
      if (k.indexOf('wacount:') === 0 && k !== today) store.deleteProperty(k);
    });
    store.setProperty(today, String(count + n));
    return true;
  } finally {
    lock.releaseLock();
  }
}

/**
 * Sends each file as one WhatsApp template message to each person: archive the file in Drive, open a link for
 * WATI to collect it, send. Returns a one-line summary. Never throws.
 */
function whatsappSend_(w, files, people, listName) {
  if (!waReserve_(w, files.length * people.length)) {
    return 'not sent: ' + files.length * people.length + ' more messages would pass WA_MAX_PER_DAY (' + w.maxPerDay + ')';
  }
  closeExpiredLinks();
  ensureCleanupTrigger_();
  var store = PropertiesService.getUserProperties();
  var auth = /^bearer /i.test(w.token) ? w.token : 'Bearer ' + w.token;
  var ok = 0, failed = [];
  files.forEach(function (f) {
    var labels = waLabels_(f, listName);
    var link;
    try {
      var file = archiveFolder_(labels.team, nowIst_('yyyy-MM-dd')).createFile(Utilities.newBlob(f.bytes, f.type.mime, f.name));
      file.setSharing(DriveApp.Access.ANYONE_WITH_LINK, DriveApp.Permission.VIEW);
      store.setProperty('walink:' + file.getId(), String(new Date().getTime()));
      link = 'https://drive.google.com/uc?export=download&id=' + file.getId();
    } catch (e) {
      failed.push(f.name + ' (Drive: ' + (e && e.message || e) + ')');
      return;
    }
    var requests = people.map(function (p) {
      var body = {template_name: w.template, broadcast_name: 'reports_' + listName + '_' + nowIst_('yyyy-MM-dd'),
                  // WATI numbers a template's variables: {{1}} the header document link, {{2}}-{{4}} the body
                  parameters: [{name: '1', value: link}, {name: '2', value: labels.report},
                               {name: '3', value: labels.team}, {name: '4', value: labels.period}]};
      if (w.channel) body.channel_number = w.channel;
      return {url: WA_HOST + '/' + w.tenant + '/api/v1/sendTemplateMessage?whatsappNumber=' + p.number, method: 'post',
              contentType: 'application/json', headers: {Authorization: auth}, payload: JSON.stringify(body), muteHttpExceptions: true};
    });
    var responses;
    try {
      responses = UrlFetchApp.fetchAll(requests);
    } catch (e) {
      failed.push(f.name + ' (' + (e && e.message || e) + ')');
      return;
    }
    responses.forEach(function (r, i) {
      var code = r.getResponseCode(), json = null;
      try { json = JSON.parse(r.getContentText()); } catch (e) { json = null; }
      if (code === 200 && json && json.result === true) {
        ok++;
      } else {
        var why = json && (json.info || json.message || (json.errors && json.errors.error)) || 'HTTP ' + code;
        failed.push(f.name + ' to ...' + people[i].number.slice(-4) + ' (' + String(why).slice(0, 120) + ')');
      }
    });
    store.setProperty('wa:' + listName + ':' + f.sha, JSON.stringify({at: nowIst_('yyyy-MM-dd HH:mm') + ' IST', name: f.name,
                                                                        to: people.length, file: link.split('id=')[1]}));
  });
  var line = ok + ' of ' + files.length * people.length + ' messages sent (' + files.length + ' file(s) to ' + people.length + ' number(s))';
  if (failed.length) {
    console.error('WhatsApp failures: ' + failed.join('; '));
    line += '; failed: ' + failed.slice(0, 3).join('; ') + (failed.length > 3 ? '; and ' + (failed.length - 3) + ' more' : '');
  }
  return line;
}

/** After a live email to a list: WhatsApp the same files to WA_LIST_<same name>, if WhatsApp is on. '' when off. */
function whatsappAfterEmail_(files, listName) {
  try {
    var w = readWhatsApp_();
    if (w.mode !== 'live' || !files.length) return '';
    if (w.problem) return 'not sent: ' + w.problem;
    if (!w.lists[listName]) return '';
    return whatsappSend_(w, files, w.lists[listName], listName);
  } catch (e) {
    console.error('WhatsApp failed: ' + e);
    return 'failed: ' + String(e && e.message || e);
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
      var out = {ok: true, status: cfg.mode === 'off' ? 'off' : 'ready', mode: cfg.mode, recipients: recipientsFor_(cfg, name).length,
                 multi: true, list: name || '', lists: Object.keys(cfg.lists).sort()};
      var w = readWhatsApp_();
      if (w.mode !== 'off') out.whatsapp = {mode: w.mode, numbers: name && w.lists[name] ? w.lists[name].length : 0, problem: w.problem};
      return reply_(out);
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

/** Checks the WhatsApp settings and asks WATI whether the template is approved. Sends nothing. */
function checkWhatsApp() {
  var w = readWhatsApp_();
  var lines = [
    'WhatsApp: ' + w.mode.toUpperCase() + (w.mode === 'live' ? ' (reports emailed to a list also go to its WA_LIST)' : ' (nothing is sent on WhatsApp)'),
    'WATI key: ' + (w.token ? 'set' : 'MISSING') + '; client ID: ' + (w.tenant ? 'set' : 'MISSING') + '; template: ' + w.template,
    'Test number: ' + (w.test ? '...' + w.test.number.slice(-4) : '(none)'),
    'At most ' + w.maxPerDay + ' WhatsApp messages a day'
  ].concat(Object.keys(w.lists).sort().map(function (n) {
    return 'WA list ' + n + ' (' + w.lists[n].length + '): ' + w.lists[n].map(function (p) { return '...' + p.number.slice(-4); }).join(', ');
  }));
  if (w.problem) lines.push('PROBLEM: ' + w.problem);
  if (w.token && WA_TENANT.test(w.tenant)) {
    var all = [], code = 200;
    for (var page = 1; page <= 20 && code === 200; page++) {  // WATI lists templates 100 to a page
      var r = UrlFetchApp.fetch(WA_HOST + '/' + w.tenant + '/api/v1/getMessageTemplates?pageSize=100&pageNumber=' + page, {
        headers: {Authorization: /^bearer /i.test(w.token) ? w.token : 'Bearer ' + w.token}, muteHttpExceptions: true});
      code = r.getResponseCode();
      if (code !== 200) break;
      var got = (JSON.parse(r.getContentText()) || {}).messageTemplates || [];
      all = all.concat(got);
      if (got.length < 100) break;
    }
    if (code !== 200 && !all.length) {
      lines.push('WATI answered HTTP ' + code + ': check WATI_TOKEN and WATI_CLIENT_ID');
    } else {
      var t = all.filter(function (x) { return x.elementName === w.template; })[0];
      lines.push('WATI key and client ID work. Template ' + w.template + ': ' + (t ? t.status : 'NOT FOUND among ' + all.length + ' templates in WATI'));
      if (!t) {
        var near = all.filter(function (x) { return /report|daily/i.test(String(x.elementName)); }).slice(0, 5);
        lines.push(near.length ? 'Similar names: ' + near.map(function (x) { return x.elementName + ' (' + x.status + ')'; }).join(', ') +
                   '. If one of these is yours, set WATI_TEMPLATE to its exact name.' : 'No template name contains "report" or "daily".');
      }
    }
  }
  console.log(lines.join('\n'));
}

/** Sends one sample PDF (no lead data) on WhatsApp to WA_TEST_NUMBER only, to prove the whole path works. */
function sendWhatsAppTest() {
  var w = readWhatsApp_();
  if (w.problem) throw new Error(w.problem);
  if (!w.test) throw new Error('WA_TEST_NUMBER is empty: add your own number, e.g. 9198xxxxxxxx');
  var pdf = Utilities.newBlob('<h2>WhatsApp test</h2><p>This sample has no lead data. Sent ' + nowIst_('d MMM yyyy, h:mm a') +
                              ' IST.</p>', 'text/html', 'test.html').getAs('application/pdf');
  var bytes = pdf.getBytes();
  var file = {name: 'WhatsApp_test_' + nowIst_('yyyy-MM-dd') + '.pdf', bytes: bytes, type: TYPES.pdf,
              sha: hex_(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, bytes))};
  console.log('WhatsApp test: ' + whatsappSend_(w, [file], [w.test], 'test'));
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
  Object.keys(all).filter(function (k) { return k.indexOf('wa:') === 0 && k.split(':').length === 3; }).forEach(function (k) {
    var v = JSON.parse(all[k]);
    rows.push(v.at + '  whatsapp ' + k.split(':')[1] + '  ' + v.name + '  to ' + v.to + ' number(s)');
  });
  rows.sort().reverse();
  console.log(rows.length ? rows.join('\n') : 'Nothing has been emailed yet.');
}
