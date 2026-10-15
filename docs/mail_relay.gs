/**
 * Gmail relay for the Team Elite Calling reports (analytics/daily_plan/mailer.py).
 *
 * The cloud sessions cannot use SMTP, so the report code posts each email here and this script sends it from
 * the Gmail account that deploys it, with the PDF and workbook attached as built.
 *
 * Setup (about 5 minutes, once):
 *  1. Go to https://script.google.com, signed in as the account the reports should come from. New project.
 *  2. Replace the code with this file. Set KEY below to a long random string you make up (keep it private).
 *     Check RECIPIENTS.
 *  3. Deploy > New deployment > type "Web app". Execute as: Me. Who has access: Anyone. Deploy, allow the
 *     Gmail permission, and copy the web app URL (ends in /exec).
 *  4. In the Claude cloud environment settings (session title bar > environment > Edit), add two environment
 *     variables: MAIL_RELAY_URL = the web app URL, MAIL_RELAY_KEY = the same KEY. Do not paste them in chat.
 *
 * Safety: the script only sends when the key matches, and only to the addresses in RECIPIENTS, so a leaked
 * URL cannot be used to email anyone else.
 */
const KEY = 'CHANGE-ME-to-a-long-random-string';
const RECIPIENTS = [
  // the report recipients, one address per line, lower case
  'team.leader@example.com',
];

function doPost(e) {
  const d = JSON.parse(e.postData.contents);
  if (!d.key || d.key !== KEY || KEY.indexOf('CHANGE-ME') === 0) {
    return ContentService.createTextOutput('forbidden');
  }
  const asked = (d.to || []).map(function (x) { return String(x).trim().toLowerCase(); }).filter(String);
  const to = asked.length ? RECIPIENTS.filter(function (r) { return asked.indexOf(r) >= 0; }) : RECIPIENTS;
  if (!to.length) {
    return ContentService.createTextOutput('no allowed recipients');
  }
  const files = (d.attachments || []).map(function (a) {
    return Utilities.newBlob(Utilities.base64Decode(a.content), a.mimeType, a.filename);
  });
  GmailApp.sendEmail(to.join(','), d.subject, d.text || '', {
    htmlBody: d.html || '', attachments: files, name: 'Team Elite Calling reports',
  });
  return ContentService.createTextOutput('ok ' + to.length);
}
