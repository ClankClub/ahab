// Runs the Google relay's "Check the request" code against calendar requests (and a few old ones).
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const code = w.nodes.find(n => n.name === 'Check the request').parameters.jsCode;
const check = r => new Function('$input', code)({ first: () => ({ json: { body: r } }) })[0].json.refused;
const E = 'https://www.googleapis.com/calendar/v3/calendars/primary/events';
const ev = { summary: 'Dentist', start: { dateTime: '2026-10-20T14:00:00-04:00' }, end: { dateTime: '2026-10-20T15:00:00-04:00' } };
const q = { sendUpdates: 'none' };
const cases = [
  ['add event', { service: 'calendar', method: 'POST', url: E, query: q, body: ev }, true],
  ['add with reminder, place, notes', { service: 'calendar', method: 'POST', url: E, query: q, body: { ...ev, location: 'x', description: 'y', reminders: { useDefault: false, overrides: [{ method: 'popup', minutes: 30 }] } } }, true],
  ['read one', { service: 'calendar', method: 'GET', url: E + '/abc123def', query: {} }, true],
  ['change one', { service: 'calendar', method: 'PATCH', url: E + '/abc123def', query: q, body: { summary: 'New' } }, true],
  ['guests', { service: 'calendar', method: 'POST', url: E, query: q, body: { ...ev, attendees: [{ email: 'a@b.c' }] } }, false],
  ['guests added later', { service: 'calendar', method: 'PATCH', url: E + '/abc123def', query: q, body: { attendees: [{ email: 'a@b.c' }] } }, false],
  ['meet link', { service: 'calendar', method: 'POST', url: E, query: q, body: { ...ev, conferenceData: {} } }, false],
  ['cancel via status', { service: 'calendar', method: 'PATCH', url: E + '/abc123def', query: q, body: { status: 'cancelled' } }, false],
  ['public visibility', { service: 'calendar', method: 'PATCH', url: E + '/abc123def', query: q, body: { visibility: 'public' } }, false],
  ['sends updates', { service: 'calendar', method: 'POST', url: E, query: { sendUpdates: 'all' }, body: ev }, false],
  ['no sendUpdates', { service: 'calendar', method: 'POST', url: E, query: {}, body: ev }, false],
  ['extra option', { service: 'calendar', method: 'POST', url: E, query: { sendUpdates: 'none', conferenceDataVersion: '1' }, body: ev }, false],
  ['delete', { service: 'calendar', method: 'DELETE', url: E + '/abc123def', query: q }, false],
  ['put', { service: 'calendar', method: 'PUT', url: E + '/abc123def', query: q, body: ev }, false],
  ['quickAdd', { service: 'calendar', method: 'POST', url: E + '/quickAdd', query: q, body: {} }, false],
  ['import', { service: 'calendar', method: 'POST', url: E + '/import', query: q, body: ev }, false],
  ['move to other calendar', { service: 'calendar', method: 'POST', url: E + '/abc123def/move', query: q, body: {} }, false],
  ['owner calendar', { service: 'calendar', method: 'POST', url: 'https://www.googleapis.com/calendar/v3/calendars/owner%40x.com/events', query: q, body: ev }, false],
  ['calendar acl', { service: 'calendar', method: 'POST', url: 'https://www.googleapis.com/calendar/v3/calendars/primary/acl', query: q, body: {} }, false],
  ['events list read', { service: 'calendar', method: 'GET', url: E, query: {} }, true],
  ['tasks still work', { service: 'tasks', method: 'POST', url: 'https://tasks.googleapis.com/tasks/v1/lists/x/tasks', query: {}, body: { title: 't' } }, true],
  ['drive share still refused', { service: 'drive', method: 'POST', url: 'https://www.googleapis.com/drive/v3/files/x/permissions', body: {} }, false],
];
let bad = 0;
for (const [label, req, ok] of cases) {
  const refused = check(req);
  const pass = ok ? !refused : !!refused;
  if (!pass) bad++;
  console.log((pass ? 'ok   ' : 'FAIL ') + label + (refused ? ' -> ' + refused : ''));
}
console.log(bad ? bad + ' failed' : 'all ' + cases.length + ' calendar relay cases ok');
process.exit(bad ? 1 : 0);
