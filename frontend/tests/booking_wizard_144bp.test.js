/* Build 144bp — themed client booking wizard + theme picker in settings.
   Run: node booking_wizard_144bp.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');
const book = R('book.html');
const pp = R('static/js/pages/publicpages.js');
const idx = R('index.html');

check('Four theme skins defined', /data-theme="1"/.test(book) && /data-theme="2"/.test(book) && /data-theme="3"/.test(book) && /data-theme="4"/.test(book));
check('Theme applied from config', /setAttribute\('data-theme'/.test(book) && /c\.theme/.test(book));
check('Stepped wizard (4 steps + progress)', /state\.step/.test(book) && /\['Service','Time','Details','Confirm'\]/.test(book));
check('Multi-day calendar via /days', /\/days\?start=/.test(book) && /class="days"/.test(book) && /Next week/.test(book));
check('Slots request passes service_id', /\/slots\?date=\$\{state\.date\}&service_id=/.test(book));
check('Logo + brand colour honoured', /c\.logo_data/.test(book) && /--acc/.test(book) && /c\.color/.test(book));
check('Create posts same contract', /method:'POST'[\s\S]{0,200}service_id: state\.service, date: state\.date, time: state\.time/.test(book));
check('Settings theme picker present', /pp-themes/.test(pp) && /pickTheme\(/.test(pp));
check('Save posts booking_theme', /booking_theme:\s*this\._theme/.test(pp));
check('No emoji in book.html', !/[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(book));
check('Build bumped', /build 144(b[p-z]|[c-z][a-z])/.test(idx));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
