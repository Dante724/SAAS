/* Build 144ca — server-side saved views: list-extras syncs to /api/prefs.
   Run: node list_extras_sync_144ca.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const lx = R('static/js/list-extras.js');

check('has _pull that GETs /api/prefs/listview:', /_pull[\s\S]{0,200}get\(['"`]\/api\/prefs\/['"`]?\s*\+?\s*this\._pk\(entity\)/.test(lx) || /get\('\/api\/prefs\/' \+ this\._pk\(entity\)\)/.test(lx));
check('has _push that PUTs /api/prefs/', /_push[\s\S]{0,220}put\('\/api\/prefs\/' \+ this\._pk\(entity\)/.test(lx));
check('_pk builds the listview:<entity> key', /_pk\(entity\)\s*\{[\s\S]{0,60}listview:['"`]\s*\+\s*entity/.test(lx));
check('attach pulls server prefs before rendering', /await this\._pull\(entity\)/.test(lx));
check('_save pushes unless fromServer', /_save\(entity, st, fromServer\)[\s\S]{0,140}if \(!fromServer\) this\._push\(entity\)/.test(lx));
check('_saveViews pushes unless fromServer', /_saveViews\(entity, v, fromServer\)[\s\S]{0,140}if \(!fromServer\) this\._push\(entity\)/.test(lx));
check('server writes into local cache with fromServer flag', /this\._save\(entity, \{[\s\S]{0,80}\}, true\)/.test(lx) && /this\._saveViews\(entity, v\.views, true\)/.test(lx));
check('pull runs once per session', /_pulled\[entity\]/.test(lx));
check('Build bumped to 144ca+', /build 144(c[a-z]|[d-z][a-z])/.test(R('index.html')));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
