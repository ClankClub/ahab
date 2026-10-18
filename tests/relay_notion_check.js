// Runs the Notion relay's "Check the request" code: what it lets through, and what it refuses.
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const code = w.nodes.find(n => n.name === 'Check the request').parameters.jsCode;
const check = r => new Function('$input', code)({ first: () => ({ json: { body: r } }) })[0].json.refused;
const N = 'https://api.notion.com/v1/';
const ID = '3fd0cd27dc04800abc14edb2d6f562c3';
const DASHED = '3fd0cd27-dc04-800a-bc14-edb2d6f562c3';
const cases = [
  ['search', { method: 'POST', url: N + 'search', body: { query: 'x' } }, true],
  ['read a page', { method: 'GET', url: N + 'pages/' + ID }, true],
  ['create a page', { method: 'POST', url: N + 'pages', body: { parent: { page_id: ID } } }, true],
  ['create a database', { method: 'POST', url: N + 'databases', body: { parent: { page_id: ID }, properties: {} } }, true],
  ['move a page', { method: 'POST', url: N + 'pages/' + ID + '/move', body: { parent: { type: 'page_id', page_id: ID } } }, true],
  ['trash a page', { method: 'PATCH', url: N + 'pages/' + ID, body: { in_trash: true } }, true],
  ['trash a database', { method: 'PATCH', url: N + 'databases/' + DASHED, body: { in_trash: true } }, true],
  ['delete a block', { method: 'DELETE', url: N + 'blocks/' + DASHED }, true],
  ['trash slipped into an edit', { method: 'PATCH', url: N + 'pages/' + ID, body: { in_trash: true, properties: {} } }, false],
  ['untrash', { method: 'PATCH', url: N + 'pages/' + ID, body: { in_trash: false } }, false],
  ['archived in a new page', { method: 'POST', url: N + 'pages', body: { archived: true } }, false],
  ['trash a block by PATCH', { method: 'PATCH', url: N + 'blocks/' + ID, body: { in_trash: true } }, false],
  ['DELETE a page', { method: 'DELETE', url: N + 'pages/' + ID }, false],
  ['DELETE a block\'s children', { method: 'DELETE', url: N + 'blocks/' + ID + '/children' }, false],
  ['comments', { method: 'POST', url: N + 'comments', body: {} }, false],
  ['users', { method: 'GET', url: N + 'users' }, false],
  ['PUT', { method: 'PUT', url: N + 'pages/' + ID, body: {} }, false],
  ['query in the url', { method: 'GET', url: N + 'pages/' + ID + '?x=1' }, false],
  ['another host', { method: 'GET', url: 'https://example.com/v1/pages/' + ID }, false],
];
let bad = 0;
for (const [label, req, ok] of cases) {
  const refused = check(req);
  const pass = ok ? !refused : !!refused;
  if (!pass) bad++;
  console.log((pass ? 'ok   ' : 'FAIL ') + label + (refused ? ' -> ' + refused : ''));
}
console.log(bad ? bad + ' failed' : 'all ' + cases.length + ' notion relay cases ok');
process.exit(bad ? 1 : 0);
