// Runs the email relay's "Check the email" code against sample requests. The
// real list of hashes is swapped for test addresses, since the real ones are
// the owner's friends.
const fs = require('fs');
const crypto = require('crypto');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
let code = w.nodes.find(n => n.name === 'Check the email').parameters.jsCode;
const sha = a => crypto.createHash('sha256').update(String(a || '').trim().toLowerCase()).digest('hex');
if (!/^const ALLOWED = \[("[0-9a-f]{64}",?\s*)+\];$/m.test(code)) { console.log('FAIL the list is not only hashes'); process.exit(1); }
code = code.replace(/^const ALLOWED = .*$/m, 'const ALLOWED = ' + JSON.stringify([sha('owner@example.com'), sha('friend@example.com')]) + ';');
let store = {};
const check = body => new Function('$input', '$getWorkflowStaticData', 'Buffer', code)(
  { first: () => ({ json: { body, toHash: sha(body.to), bccHash: sha(body.bcc) } }) }, () => store, Buffer)[0].json;
const ok = { to: 'friend@example.com', bcc: 'owner@example.com', subject: 'Hi', body: 'Hello there.' };
const cases = [
  ['plain send', ok, true],
  ['case and spaces', { ...ok, to: ' Friend@Example.com ' }, false],   // the regex wants a bare address
  ['upper case', { ...ok, to: 'Friend@Example.COM' }, true],
  ['to the owner, no bcc', { to: 'owner@example.com', subject: 'Hi', body: 'x' }, true],
  ['stranger', { ...ok, to: 'stranger@example.com' }, false],
  ['stranger as bcc', { ...ok, bcc: 'stranger@example.com' }, false],
  ['two recipients', { ...ok, to: 'friend@example.com, stranger@example.com' }, false],
  ['cc field', { ...ok, cc: 'stranger@example.com' }, false],
  ['header in subject', { ...ok, subject: 'Hi\r\nBcc: stranger@example.com' }, false],
  ['empty body', { ...ok, body: '' }, false],
  ['huge body', { ...ok, body: 'x'.repeat(20001) }, false],
  ['reply in thread', { ...ok, threadId: '18f2a3b4c5d6e7f8', inReplyTo: '<abc@mail.gmail.com>' }, true],
  ['bad thread', { ...ok, threadId: '../x' }, false],
  ['header in inReplyTo', { ...ok, inReplyTo: '<a>\r\nBcc: s@example.com' }, false],
  ['object to', { ...ok, to: ['friend@example.com'] }, false],
];
let bad = 0;
for (const [label, req, want] of cases) {
  const out = check(req);
  const pass = want ? !out.refused : !!out.refused;
  if (!pass) bad++;
  console.log((pass ? 'ok   ' : 'FAIL ') + label + (out.refused ? ' -> ' + out.refused : ''));
}
// The message it builds: only these headers, the body intact.
const raw = Buffer.from(check({ ...ok, subject: 'Café', body: 'Line one\nLine two', inReplyTo: '<m@x>' }).send.raw, 'base64').toString('utf8');
const [head, text] = raw.split('\r\n\r\n');
const names = head.split('\r\n').map(l => l.split(':')[0]);
const want = ['To', 'Bcc', 'Subject', 'MIME-Version', 'Content-Type', 'Content-Transfer-Encoding', 'In-Reply-To', 'References'];
if (JSON.stringify(names) !== JSON.stringify(want)) { bad++; console.log('FAIL headers ' + names); }
if (Buffer.from(text.replace(/\r\n/g, ''), 'base64').toString('utf8') !== 'Line one\r\nLine two') { bad++; console.log('FAIL body'); }
if (!head.includes('Subject: =?UTF-8?B?' + Buffer.from('Café').toString('base64') + '?=')) { bad++; console.log('FAIL subject'); }
// The daily cap.
store = {};
let sent = 0;
for (let i = 0; i < 35; i++) if (!check(ok).refused) sent++;
if (sent !== 30) { bad++; console.log('FAIL cap let ' + sent + ' through'); } else console.log('ok   cap at 30');
console.log(bad ? bad + ' failed' : 'all passed');
process.exit(bad ? 1 : 0);
