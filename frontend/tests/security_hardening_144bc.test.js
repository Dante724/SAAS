/* Build 144bc — security hardening (frontend).
   Run: NODE_PATH=/tmp/b144test/node_modules node security_hardening_144bc.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const ct = R('static/js/category-tree.js');

// XSS: category names must be escaped for display, never raw-interpolated.
check('Name escaped for display (_esc)', /_esc\(n\.name\)/.test(ct));
check('_esc helper uses textContent', /_esc\(s\)\s*\{[\s\S]*textContent/.test(ct));
check('No raw name in ct-nm span', !/ct-nm">\$\{n\.name\}/.test(ct));

// rename() takes only an id and looks the name up (no name in the handler string)
check('rename handler passes id only', /ondblclick="CategoryTree\.rename\('\$\{n\.id\}'\)"/.test(ct));
check('rename looks name up via _find', /rename\(id\)\s*\{[\s\S]*_find\(this\._tree, id\)/.test(ct));
check('No name arg baked into any onclick handler', !/CategoryTree\.\w+\('\$\{n\.id\}',\s*'\$\{n\.name\}'/.test(ct));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
