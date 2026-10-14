/* Build 144dc — estimate statuses render as the frosted-glass chip (Option B);
   work-order statuses keep the solid pill; conversion flips estimate → WO keys. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');

const root = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');
const coreJs = read('static/js/core.js');
const css = read('static/css/design.css');
const idx = read('index.html');

// The estimate chip CSS is the new Option-B look.
const block = css.slice(css.indexOf('.badge.badge-est {'), css.indexOf('}', css.indexOf('.badge.badge-est::before')) + 1);
assert(/backdrop-filter:\s*blur/.test(block), 'estimate chip is frosted glass (blur)');
assert(/color-mix\(in srgb, var\(--est\)/.test(block), 'estimate chip tints from the status colour');
assert(/box-shadow:\s*0 0 0 3px color-mix/.test(block), 'estimate dot has a glowing ring');
assert(/build 144(dc|d[d-z]|[e-z][a-z])/.test(idx), 'index bumped to 144dc');

// badge() routing: estimate keys -> badge-est with the accent colour; WO -> badge-solid.
// Extract just the pure pieces (no DOM deps) and eval them in isolation.
const grab = (startMarker) => {
  const i = coreJs.indexOf(startMarker);
  assert(i >= 0, `core.js has ${startMarker}`);
  return coreJs.slice(i, coreJs.indexOf('\n}', coreJs.indexOf('function badge', i)) + 2);
};
const statusMapSrc = coreJs.slice(coreJs.indexOf('const statusMap'), coreJs.indexOf('};', coreJs.indexOf('const estStatusMap')) + 2);
const badgeSrc = coreJs.slice(coreJs.indexOf('function badge'), coreJs.indexOf('\n}', coreJs.indexOf('function badge')) + 2);
const badge = new Function(statusMapSrc + '\n' + badgeSrc + '\nreturn badge;')();

const est = badge('est_sent');
assert(/badge-est/.test(est) && /--est:#0ea5e9/.test(est) && /Sent/.test(est), `estimate status uses the chip (got ${est})`);

const wo = badge('new');
assert(/badge-solid/.test(wo) && !/badge-est/.test(wo), `work-order status keeps the solid pill (got ${wo})`);

// The distinct classes guarantee estimate != work order visually.
assert(!/badge-est/.test(badge('in_repair')), 'a WO status never renders as an estimate chip');
// After convert the document carries a WO key, so it renders WO-style automatically.
assert(/badge-solid/.test(badge('done')), 'converted work order shows the baseline pill');

console.log('estimate_status_144dc: OK');
