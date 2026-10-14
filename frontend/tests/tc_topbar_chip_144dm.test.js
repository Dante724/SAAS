/* Build 144dm — the global time-clock is a compact chip docked in the topbar
   (not a fixed bottom-right pod), and it expands to a popover with both clocks
   and their buttons. This keeps the bottom-right corner clear for page actions. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const ts = read('static/js/pages/timesheets.js');
const css = read('static/css/shell.css');
const idx = read('index.html');

assert(/build 144(dm|d[n-z]|[e-z][a-z])/.test(idx), 'index bumped to 144dm');

// A persistent topbar slot exists for the clock.
assert(/id="tc-slot"/.test(idx), 'index has a persistent #tc-slot in the topbar');

// The widget mounts into that slot, not document.body.
assert(/getElementById\('tc-slot'\)/.test(ts), 'mount targets #tc-slot');

// Collapsed chip + expand/collapse behaviour.
assert(/toggle\(\)\s*\{[^}]*_open\s*=\s*!this\._open/.test(ts), 'a toggle() opens/closes the popover');
assert(/tc-chip/.test(ts) && /tc-chip/.test(css), 'chip is rendered and styled');
assert(/tc-chip-time/.test(ts), 'chip shows the live time');
assert(/tc-dot-on|tc-dot-off/.test(ts), 'chip dot reflects running state');

// Expanded popover carries both clocks and all buttons.
assert(/tc-panel/.test(ts) && /tc-panel/.test(css), 'popover panel rendered and styled');
assert(/TimeClock\.stop\(\)/.test(ts) && /TimeClock\.pickOrder\(true\)/.test(ts), 'stop + switch in panel');
assert(/_shiftHtml\(\)/.test(ts), 'attendance clock included in panel');

// Closes on outside click.
assert(/_outside/.test(ts), 'outside-click closes the popover');

// The old fixed bottom-right positioning is gone.
assert(!/\.tc-widget\s*\{[^}]*position:\s*fixed/.test(css), 'tc-widget is no longer position:fixed');
assert(/\.tc-slot\s*\{/.test(css), 'topbar slot styled');

console.log('tc_topbar_chip_144dm: OK');
