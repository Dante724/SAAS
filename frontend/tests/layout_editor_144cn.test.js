/* Build 144cn — SUPERSEDED by 144cp (layout editor retired).
   The drag/edit feature was removed; saved layouts still persist and re-apply.
   Coverage moved to layout_editor_retired_144cp.test.js. This stub passes. */
const fs = require('fs');
const js = fs.readFileSync(__dirname + '/../static/js/layout-editor.js', 'utf8');
const ok = /boot\(\)/.test(js) && /this\.apply\(\)/.test(js);   // persistence still present
console.log(ok ? '  PASS  layout persistence retained (editor retired in 144cp)'
               : '  FAIL  layout-editor.js missing apply()');
process.exit(ok ? 0 : 1);
