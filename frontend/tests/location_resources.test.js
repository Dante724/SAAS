/* Build 144d — per-location resources managed inside the location edit panel.
   Run: NODE_PATH=<dir with jsdom> node location_resources.test.js               */
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const { JSDOM } = require('jsdom');

const results = [];
const check = (name, cond, detail) => {
  results.push(!!cond);
  console.log(`  ${cond ? 'PASS' : 'FAIL'}  ${name}${cond ? '' : '  ↳ ' + (detail || '')}`);
};
const jsPath = path.join(__dirname, '..', 'static', 'js', 'pages', 'settings.js');
const src = fs.readFileSync(jsPath, 'utf8');
const feedbackSrc = fs.readFileSync(path.join(__dirname, '..', 'static', 'js', 'pages', 'feedback.js'), 'utf8');

/* ── the duplicate standalone rail item is gone ── */
console.log('\nStandalone "Location resources" settings item retired:');
check('no "Location resources" rail entry in Settings',
  !/key:\s*'resources'/.test(src) && !/label:\s*'Location resources'/.test(src));
check('old "resources" route now redirects into Settings (deep links still work)',
  /Router\.register\('resources'/.test(feedbackSrc) && /_sub\s*=\s*'locations'/.test(feedbackSrc));

const dom = new JSDOM('<!doctype html><html><body><div id="modal-host"></div></body></html>',
  { url: 'http://localhost', runScripts: 'outside-only' });
const w = dom.window;

// capture what Modal.open renders so we can inspect + interact with it
w.Modal = {
  open(html) { w.document.getElementById('modal-host').innerHTML = html; },
  close() {},
};
const posted = [];
const deleted = [];
let resourcesForOrg = [
  { id: 'r1', name: 'Car lift 1', kind: 'bay', color: '#2cd4c0', location_id: 'locA' },
  { id: 'r2', name: 'Room A', kind: 'room', color: '#4f8ef7', location_id: 'locB' }, // other branch
];
w.get = async (url) => {
  if (url.startsWith('/api/resources')) return { items: resourcesForOrg };
  return { items: [] };
};
w.post = async (url, body) => { posted.push({ url, body }); resourcesForOrg.push({ id: 'r3', ...body }); return { id: 'r3' }; };
w.del = async (url) => { deleted.push(url); resourcesForOrg = resourcesForOrg.filter(r => !url.endsWith(r.id)); return {}; };
w.put = async () => ({});
w.Toast = { success(){}, error(m){ console.log('TOAST ERR:', m); }, info(){} };
w.confirm2 = async () => true;
w.icon = () => '';
w.setContent = () => {}; w.setTopbarActions = () => {};
w.Router = { register(){}, go(){}, current:'settings' };

const ctx = vm.createContext(w);
vm.runInContext(src, ctx, { filename: 'settings.js' });
const LP = vm.runInContext('LocationsSettingsPage', ctx);

(async () => {
  // --- new (unsaved) location: no resource controls, just a hint ---
  LP._items = [];
  LP.edit(null);
  let host = w.document.getElementById('modal-host').innerHTML;
  check('New-location modal shows the Location resources heading', /Location resources/.test(host));
  check('New-location modal tells you to save first (no add controls yet)',
    /Save this location first/.test(host) && !/loc-res-name/.test(host));

  // --- existing location: full add/list/delete controls ---
  LP._items = [{ id:'locA', name:'Central', working_hours:{} }];
  LP.edit('locA');
  await new Promise(r => setTimeout(r, 20));   // let _loadResources resolve
  host = w.document.getElementById('modal-host').innerHTML;
  check('Edit modal has a Working hours section (unchanged)', /Working hours/.test(host));
  check('Edit modal has the Location resources section like Working hours',
    /Location resources/.test(host) && /loc-res-name/.test(host));
  check('resources are filtered to THIS location (shows Car lift 1, not Room A)',
    /Car lift 1/.test(w.document.getElementById('loc-res-list').innerHTML) &&
    !/Room A/.test(w.document.getElementById('loc-res-list').innerHTML));

  // --- add a resource ---
  w.document.getElementById('loc-res-name').value = 'Wheel balancer';
  w.document.getElementById('loc-res-kind').value = 'equipment';
  await LP.addResource('locA');
  await new Promise(r => setTimeout(r, 10));
  check('adding a resource POSTs with this location_id',
    posted.length === 1 && posted[0].body.location_id === 'locA' &&
    posted[0].body.name === 'Wheel balancer' && posted[0].body.kind === 'equipment',
    JSON.stringify(posted));
  check('new resource appears in the list', /Wheel balancer/.test(w.document.getElementById('loc-res-list').innerHTML));

  // --- delete a resource ---
  await LP.delResource('r1', 'locA');
  await new Promise(r => setTimeout(r, 10));
  check('deleting a resource calls DELETE /api/resources/{id}',
    deleted.length === 1 && deleted[0] === '/api/resources/r1', deleted.join(','));
  check('deleted resource is gone from the list',
    !/Car lift 1/.test(w.document.getElementById('loc-res-list').innerHTML));

  const failed = results.filter(r => !r).length;
  console.log(failed ? `\nFAILURES ❌ (${failed})` : '\nALL PASS ✅');
  process.exit(failed ? 1 : 0);
})();
