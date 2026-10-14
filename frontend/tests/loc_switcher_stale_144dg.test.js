/* Build 144dg — a stale persisted location must be cleared BEFORE the single-branch
   early-return, or the order/estimate list filters by a dead location and shows nothing. */
const fs=require('fs'),path=require('path'),assert=require('assert');
const app=fs.readFileSync(path.resolve(__dirname,'../static/js/app.js'),'utf8');
const idx=fs.readFileSync(path.resolve(__dirname,'../index.html'),'utf8');
const fn=app.slice(app.indexOf('async mountLocationSwitcher'), app.indexOf('setLocation(id)'));
const resetAt=fn.indexOf("this.locationId = ''");
const earlyReturnAt=fn.indexOf('locs.length < 2');
assert(resetAt>=0 && earlyReturnAt>=0, 'both guards present');
assert(resetAt < earlyReturnAt, 'stale-location reset runs before the <2 early return');
assert(/localStorage\.setItem\('ros_loc', ''\)/.test(fn), 'stale reset also persists the cleared value');
assert(/build 144(dg|d[h-z]|[e-z][a-z])/.test(idx),'index bumped to 144dg');
console.log('loc_switcher_stale_144dg: OK');
