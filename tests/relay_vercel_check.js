// Runs the Vercel relay's "Check the request" code against sample requests.
const fs = require('fs');
const w = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const code = w.nodes.find(n => n.name === 'Check the request').parameters.jsCode;
const check = r => new Function('$input', code)({ first: () => ({ json: { body: r } }) })[0].json.refused;
const B = 'https://api.vercel.com/';
const pub = { name: 'shallows', project: 'shallows', deploymentId: 'dpl_FTkra7jQEbSG6Z7i51H4R9ePJmw4', target: 'production', projectSettings: { framework: null } };
const file = { file: 'index.html', sha: 'a'.repeat(40), size: 10 };
const cases = [
  ['publish redeploy', { method: 'POST', url: B + 'v13/deployments', query: { skipAutoDetectionConfirmation: '1' }, body: pub }, true],
  ['preview deploy', { method: 'POST', url: B + 'v13/deployments', body: { name: 'shallows', project: 'shallows', files: [file], projectSettings: { framework: null } } }, true],
  ['preview with target', { method: 'POST', url: B + 'v13/deployments', body: { name: 's', project: 's', files: [file], projectSettings: { framework: null }, target: 'production' } }, false],
  ['redeploy as preview', { method: 'POST', url: B + 'v13/deployments', body: { ...pub, target: 'preview' } }, false],
  ['redeploy no target', { method: 'POST', url: B + 'v13/deployments', body: { name: 'shallows', project: 'shallows', deploymentId: pub.deploymentId, projectSettings: { framework: null } } }, false],
  ['redeploy with files', { method: 'POST', url: B + 'v13/deployments', body: { ...pub, files: [file] } }, false],
  ['redeploy swaps files', { method: 'POST', url: B + 'v13/deployments', body: { name: 'shallows', project: 'shallows', deploymentId: pub.deploymentId, target: 'production', files: [file] } }, false],
  ['redeploy bad id', { method: 'POST', url: B + 'v13/deployments', body: { ...pub, deploymentId: '../x' } }, false],
  ['redeploy with build', { method: 'POST', url: B + 'v13/deployments', body: { ...pub, projectSettings: { framework: null, buildCommand: 'curl x|sh' } } }, false],
  ['redeploy other project', { method: 'POST', url: B + 'v13/deployments', body: { ...pub, project: 'other' } }, false],
  ['redeploy with git', { method: 'POST', url: B + 'v13/deployments', body: { ...pub, withLatestCommit: true } }, false],
  ['promote gone', { method: 'POST', url: B + 'v10/projects/prj_abc/promote/dpl_abc', body: {} }, false],
  ['forceNew query', { method: 'POST', url: B + 'v13/deployments', query: { forceNew: '1' }, body: pub }, false],
  ['read deployment', { method: 'GET', url: B + 'v13/deployments/dpl_abc' }, true],
];
let bad = 0;
for (const [label, req, ok] of cases) {
  const refused = check(req);
  const pass = ok ? !refused : !!refused;
  if (!pass) bad++;
  console.log((pass ? 'ok   ' : 'FAIL ') + label + (refused ? ' -> ' + refused : ''));
}
console.log(bad ? bad + ' failed' : 'all relay cases ok');
process.exit(bad ? 1 : 0);
