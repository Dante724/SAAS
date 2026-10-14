/* Build 144bn — per-location 7-day working-hours editor.
   Run: node location_hours_144bn.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const settings = R('static/js/pages/settings.js');
const idx = R('index.html');

check('DAYS covers full week mon..sun',
  /DAYS\s*=\s*\[\['mon'[\s\S]*?\['sun'/.test(settings));
check('Each weekday row has open + close time inputs',
  /type="time" id="wh-\$\{k\}-open"/.test(settings) &&
  /type="time" id="wh-\$\{k\}-close"/.test(settings) &&
  /DAYS\.map\(dayRow\)/.test(settings));
check('Per-day Closed toggle + enable/disable wiring',
  /id="wh-\$\{k\}-closed"/.test(settings) &&
  /_toggleDay\('\$\{k\}'\)/.test(settings) &&
  /_toggleDay\(k\)[\s\S]{0,200}\.disabled = closed/.test(settings));
check('_collectHours builds {open,close} or {closed} per day',
  /_collectHours\(\)[\s\S]{0,400}closed \? \{ closed: true \} : \{ open:[\s\S]{0,120}close:/.test(settings));
check('save() sends working_hours to locations endpoint',
  /working_hours:\s*this\._collectHours\(\)/.test(settings) &&
  /\/api\/staff\/locations/.test(settings));
check('List shows hours summary column',
  /_hoursSummary\(l\.working_hours\)/.test(settings));
check('Build bumped', /build 144(b[n-z]|[c-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
