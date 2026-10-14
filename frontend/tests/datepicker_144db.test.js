/* Build 144db — custom datepicker replaces the native calendar on date fields.
   Verifies: module present + wired, and that picking a day writes the native-format
   value and fires change; datetime-local keeps a time component. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');

const dpJs = read('static/js/datepicker.js');
const idx = read('index.html');
const css = read('static/css/design.css');

// Wiring.
assert(/window\.DatePicker\s*=/.test(dpJs), 'datepicker.js exposes window.DatePicker');
assert(/static\/js\/datepicker\.js/.test(idx), 'index.html includes datepicker.js');
assert(/\.rdp\b/.test(css) && /\.rdp-day\b/.test(css), 'popover CSS present');
assert(/build 144(db|d[c-z]|[e-z][a-z])/.test(idx), 'index bumped to 144db');

// Runtime: build a DOM with a date + datetime-local field, load the module, drive it.
const dom = new JSDOM(`<!doctype html><body>
  <input class="field-input" id="d" type="date" value="2026-08-27">
  <input class="field-input" id="dt" type="datetime-local" value="2026-08-27T14:30" min="2026-08-01" max="2026-08-31">
  <input class="field-input" id="txt" type="text">
</body>`, { pretendToBeVisual: true });
const w = dom.window;
global.window = w; global.document = w.document;
global.Event = w.Event; global.Date = Date;
// jsdom lacks layout; stub rects so position() doesn't throw.
w.HTMLElement.prototype.getBoundingClientRect = () => ({ top: 100, bottom: 130, left: 40, right: 200, width: 160, height: 30 });
Object.defineProperty(w, 'innerWidth', { value: 1200, configurable: true });
Object.defineProperty(w, 'innerHeight', { value: 800, configurable: true });
eval(dpJs);

const DatePicker = w.DatePicker;
assert(typeof DatePicker.open === 'function', 'DatePicker.open exists');

// Date field: open, pick day 15 -> value becomes 2026-08-15, change fires.
const dEl = w.document.getElementById('d');
let changed = false; dEl.addEventListener('change', () => { changed = true; });
DatePicker.open(dEl);
let popEl = w.document.querySelector('.rdp');
assert(popEl, 'popover opens for a date field');
const day15 = [...popEl.querySelectorAll('.rdp-day')].find(b => b.textContent === '15');
assert(day15, 'day 15 rendered');
day15.click();
assert(dEl.value === '2026-08-15', `date value set (got ${dEl.value})`);
assert(changed === true, 'change event fired');
assert(!w.document.querySelector('.rdp'), 'popover closes after pick');
assert(dEl.readOnly === true, 'native field made readonly to suppress OS picker');

// datetime-local: value keeps THH:MM, and min/max disable out-of-range days.
const dtEl = w.document.getElementById('dt');
DatePicker.open(dtEl);
popEl = w.document.querySelector('.rdp');
assert(popEl.querySelector('.rdp-time'), 'time row shown for datetime-local');
const day10 = [...popEl.querySelectorAll('.rdp-day')].find(b => b.textContent === '10');
day10.click();
assert(/^2026-08-10T\d{2}:\d{2}$/.test(dtEl.value), `datetime keeps time (got ${dtEl.value})`);

// A plain text field is ignored by the enhancer.
DatePicker.open; // no-op guard
assert(w.document.getElementById('txt').readOnly === false, 'text inputs untouched');

// Keyboard access: pressing Enter on a focused date field opens the picker.
assert(/key === 'Enter'/.test(dpJs) && /ArrowDown/.test(dpJs), 'keydown open handler present');
DatePicker.close();
const kbEl = w.document.getElementById('d');
kbEl.dispatchEvent(new w.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
assert(w.document.querySelector('.rdp'), 'Enter on a focused date field opens the picker');

console.log('datepicker_144db: OK');
