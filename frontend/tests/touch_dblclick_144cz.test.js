/* Build 144cz — touch fallback for double-click-to-open list rows.
   A single tap on a coarse pointer must re-dispatch as the row's ondblclick;
   the same tap on a fine pointer (desktop) must do nothing. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM } = require('jsdom');

const appJs = fs.readFileSync(path.resolve(__dirname, '../static/js/app.js'), 'utf8');
const idx = fs.readFileSync(path.resolve(__dirname, '../index.html'), 'utf8');

// Static wiring.
assert(/pointer:coarse/.test(appJs), 'app.js gates the fallback on a coarse pointer');
assert(/tr\[ondblclick\]/.test(appJs), 'app.js targets dblclick rows');
assert(/dispatchEvent\(new MouseEvent\('dblclick'/.test(appJs), 'app.js re-dispatches dblclick');
assert(/build 144(cz|[d-z][a-z])/.test(idx), 'index bumped to 144cz');

// Extract the fallback block and run it under both pointer types.
const start = appJs.indexOf("Build 144cz");
const block = appJs.slice(appJs.indexOf('if (typeof window', start), appJs.indexOf('\n}\n', start) + 2);

function run(coarse) {
  const dom = new JSDOM(
    `<!doctype html><table><tbody>
       <tr id="row" ondblclick="window.__opened=true">
         <td>cell</td><td><button id="btn">Edit</button></td>
       </tr></tbody></table>`,
    { runScripts: 'outside-only' });
  const w = dom.window;
  w.matchMedia = (q) => ({ matches: coarse && /coarse/.test(q) });
  w.__opened = false;
  // Wire the inline ondblclick handler (jsdom outside-only doesn't auto-run it).
  w.document.getElementById('row').addEventListener('dblclick', () => { w.__opened = true; });
  // In a real browser MouseEvent is a global; expose the window's here so the
  // extracted block's `new MouseEvent(...)` resolves.
  global.MouseEvent = w.MouseEvent;
  const fn = new Function('window', 'document', block);
  fn(w, w.document);
  return w;
}

// Coarse pointer: tapping the row opens it; tapping the button does not.
let w = run(true);
w.document.getElementById('row').querySelector('td').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
assert(w.__opened === true, 'coarse: tap on a cell opens the row');

w = run(true);
w.document.getElementById('btn').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
assert(w.__opened === false, 'coarse: tap on an inner button does not open the row');

// Fine pointer: the listener is never attached, so a tap does nothing.
w = run(false);
w.document.getElementById('row').querySelector('td').dispatchEvent(new w.MouseEvent('click', { bubbles: true }));
assert(w.__opened === false, 'fine: single click does not open (dblclick still required)');

console.log('touch_dblclick_144cz: OK');
