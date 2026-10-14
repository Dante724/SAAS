/* Build 144da — Country auto-fills currency + timezone (interlinked fields).
   Covers the shared Geo table, the General-settings handler, and the signup handler. */
const fs = require('fs');
const path = require('path');
const assert = require('assert');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
const read = (p) => fs.readFileSync(path.join(root, p), 'utf8');

const geoJs = read('static/js/geo.js');
const authJs = read('static/js/auth-flows.js');
const feedbackJs = read('static/js/pages/feedback.js');
const idx = read('index.html');

// Wiring is present.
assert(/window\.Geo\s*=/.test(geoJs), 'geo.js exposes window.Geo');
assert(/static\/js\/geo\.js/.test(idx), 'index.html includes geo.js');
assert(/GeneralPage\.countryChanged\(\)/.test(feedbackJs), 'general settings country onchange wired');
assert(/countryChanged\s*\(\)/.test(feedbackJs), 'GeneralPage.countryChanged defined');
assert(/AuthFlows\.countryChanged\(\)/.test(idx), 'signup country onchange wired');
assert(/countryChanged\s*\(\)/.test(authJs), 'AuthFlows.countryChanged defined');
assert(/country:\s*\(document\.getElementById\('su-country'\)/.test(authJs), 'signup submits country');
assert(/timezone:\s*this\._tz/.test(authJs), 'signup submits derived timezone');
assert(/build 144(da|d[b-z]|[e-z][a-z])/.test(idx), 'index bumped to 144da');

// Geo data sanity.
const dom = new JSDOM('<!doctype html><body></body>');
global.window = dom.window; global.document = dom.window.document;
eval(geoJs);
const Geo = dom.window.Geo;
assert(Geo.COUNTRIES.length >= 40, 'Geo has a real country list');
const ae = Geo.meta('ae');   // case-insensitive
assert(ae && ae.currency_code === 'AED' && /Dubai/.test(ae.timezone) && ae.region === 'mena', 'UAE meta correct');
const us = Geo.meta('US');
assert(us.currency_code === 'USD' && us.region === 'us', 'US meta correct');
assert(Geo.meta('ZZ') === null && Geo.meta('') === null, 'unknown/empty -> null');
// Every row is complete.
for (const c of Geo.COUNTRIES) {
  assert(c.code && c.name && c.currency_code && c.symbol && c.timezone && c.region,
    `row ${c.code} is complete`);
}

// General-settings handler: choosing a country fills ccode, symbol, and adds+selects tz.
{
  const d = new JSDOM(`<!doctype html><body>
    <select id="g-country"><option value="">-</option><option value="AE">UAE</option></select>
    <input id="g-ccode" value="INR"><input id="g-currency" value="₹">
    <select id="g-tz"><option>Asia/Kolkata</option></select>
  </body>`);
  const w = d.window;
  w.Geo = Geo; w.Toast = { info() {} };
  // Extract the countryChanged() body from GeneralPage and run it bound to w.
  const body = feedbackJs.slice(feedbackJs.indexOf('countryChanged() {') + 'countryChanged() {'.length);
  const fnSrc = body.slice(0, body.indexOf('\n  },'));
  const run = new Function('document', 'Geo', 'Toast', 'Option', fnSrc);
  w.document.getElementById('g-country').value = 'AE';
  run(w.document, Geo, w.Toast, w.Option);
  assert(w.document.getElementById('g-ccode').value === 'AED', 'ccode filled to AED');
  assert(w.document.getElementById('g-currency').value === 'AED', 'symbol filled');
  assert(w.document.getElementById('g-tz').value === 'Asia/Dubai', 'timezone added + selected');
}

console.log('geo_autofill_144da: OK');
