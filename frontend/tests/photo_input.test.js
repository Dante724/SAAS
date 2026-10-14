/* Build 144aq — shared take-photo/choose picker wired everywhere a photo is added.
   Run: NODE_PATH=/tmp/b144test/node_modules node photo_input.test.js */
const fs = require('fs');
const path = require('path');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

const pi = R('static/js/photo-input.js');
const idx = R('index.html');
const orders = R('static/js/pages/orders.js');
const products = R('static/js/pages/products.js');
const feedback = R('static/js/pages/feedback.js');
const feed = R('static/js/feed.js');
const css = R('static/css/design.css');

// component
check('PhotoInput defined + registered', /const PhotoInput\s*=/.test(pi) && /photo-input\.js/.test(idx));
check('Offers Take photo + Choose file', /Take photo/.test(pi) && /Choose file/.test(pi));
check('Camera via getUserMedia', /getUserMedia/.test(pi) && /pi-video/.test(pi));
check('Graceful fallback to file when no camera', /choose a file instead/.test(pi) && /chooseFile\(\)/.test(pi));
check('Downscales to JPEG data URL', /toDataURL\('image\/jpeg'/.test(pi) && /_scale\(/.test(pi));
check('Stops the camera stream', /getTracks\(\)\.forEach\(t => t\.stop\(\)\)/.test(pi));
check('Styled (choices + camera view)', /\.pi-choice/.test(css) && /\.pi-cam/.test(css));
// 144au — the picker must STACK over the caller's form (both chooser + camera view),
// otherwise Modal.open's closeAll() destroys the product form and Create fails.
check('Chooser + camera stack over the caller (both use stack=true)',
  (pi.match(/'modal-narrow',\s*true\)/g) || []).length >= 2);

// wired everywhere
check('Asset photo uses PhotoInput', /PhotoInput\.capture\(\{[\s\S]*image_data: dataUrl/.test(orders));
check('Product image uses PhotoInput', /pickImage\(\)/.test(products) && /PhotoInput\.capture/.test(products));
check('Company logo uses PhotoInput', /pickLogo\(\)/.test(feedback) && /PhotoInput\.capture/.test(feedback));
check('Feed photo uses PhotoInput', /PhotoInput\.capture/.test(feed));
check('No emoji in picker', !/[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}]/u.test(pi));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exit(1);
