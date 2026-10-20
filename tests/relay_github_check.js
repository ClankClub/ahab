// Runs the GitHub relay's "Check the request" code against sample requests:
// as shipped (no account filled in, so only "who am I" passes), then with an
// account filled in the way setup does it.
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const shipped = w.nodes.find(n => n.name === 'Check the request').parameters.jsCode;
const filled = shipped.replace("const OWNER = 'YOUR-AGENT-GITHUB-ACCOUNT';", "const OWNER = 'example-agent';");
if (filled === shipped) { console.log('FAIL the placeholder is missing'); process.exit(1); }
const run = (code, r) => new Function('$input', code)({ first: () => ({ json: { body: r } }) })[0].json.refused;
const B = 'https://api.github.com/';
const cases = [
  ['shipped: who am I', shipped, { method: 'GET', url: B + 'user' }, true],
  ['shipped: read a repo', shipped, { method: 'GET', url: B + 'repos/example-agent/site' }, false],
  ['shipped: list repos', shipped, { method: 'GET', url: B + 'user/repos' }, false],
  ['filled: who am I', filled, { method: 'GET', url: B + 'user' }, true],
  ['filled: read own repo', filled, { method: 'GET', url: B + 'repos/example-agent/site' }, true],
  ['filled: commit to own repo', filled, { method: 'POST', url: B + 'repos/example-agent/site/git/commits', body: {} }, true],
  ['filled: someone else\'s repo', filled, { method: 'GET', url: B + 'repos/someone-else/site' }, false],
  ['filled: commit elsewhere', filled, { method: 'POST', url: B + 'repos/someone-else/site/git/blobs', body: {} }, false],
  ['filled: delete own repo', filled, { method: 'DELETE', url: B + 'repos/example-agent/site' }, false],
];
let bad = 0;
for (const [label, code, req, ok] of cases) {
  const refused = run(code, req);
  const pass = ok ? !refused : !!refused;
  if (!pass) bad++;
  console.log((pass ? 'ok   ' : 'FAIL ') + label + (refused ? ' -> ' + refused : ''));
}
console.log(bad ? bad + ' failed' : 'all relay cases ok');
process.exit(bad ? 1 : 0);
