/* Build 144dn — clock privacy + manager roster on the Timesheets page.
   The roster block is manager-gated (can_manage_payroll) and reads /roster;
   the widget endpoints stay self-scoped so an employee only sees their own clock. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const ts = read('static/js/pages/timesheets.js');
const css = read('static/css/shell.css');
const idx = read('index.html');

assert(/build 144(dn|d[o-z]|[e-z][a-z])/.test(idx), 'index bumped to 144dn');

// Roster block is gated behind the payroll-manage permission.
assert(/App\.can\('can_manage_payroll'\)/.test(ts), 'roster is manager-gated in the FE');
assert(/id="ts-roster"/.test(ts), 'roster host element rendered for managers');
assert(/loadRoster\(\)/.test(ts), 'loadRoster is called on page load');
assert(/\/api\/timeclock\/roster/.test(ts), 'roster reads the manager-only endpoint');

// Renders who is on shift + where, and running timers, with a live tick.
assert(/on_shift/.test(ts) && /location_name/.test(ts), 'shows who is clocked in and where');
assert(/ts-ros-time\[data-since\]/.test(ts), 'roster times tick live from start');
assert(/\.ts-roster\s*\{/.test(css) && /\.ts-ros-row\s*\{/.test(css), 'roster styled');

// The widget itself never queries other employees — its calls are self-scoped
// (no employee_id passed to active/shift-active/start/stop).
assert(/get\('\/api\/timeclock\/active'\)/.test(ts), 'active timer read is self-scoped');
assert(/get\('\/api\/timeclock\/shift\/active'\)/.test(ts), 'active shift read is self-scoped');

console.log('clock_roster_144dn: OK');
