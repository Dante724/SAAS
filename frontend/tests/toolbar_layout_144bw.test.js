/* Build 144bw — Work-orders layout app-wide: view switches stay top-right,
   every other action hoists into a content toolbar row.
   Run: node toolbar_layout_144bw.test.js */
const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');
const results = [];
function check(n, c, d) { results.push({ n, ok: !!c }); console.log(`  ${c ? 'PASS' : 'FAIL'}  ${n}${c ? '' : '  ↳ ' + (d || '')}`); }
const R = (p) => fs.readFileSync(path.join(__dirname, '..', p), 'utf8');

const dom = new JSDOM(`<!DOCTYPE html><div id="content"></div><div id="topbar-actions"></div>`, { pretendToBeVisual: true });
const { window } = dom;
global.window = window; global.document = window.document;
global.App = window.App = { applyPermissions() {}, _pageToolbarHTML: '' };
window.injectCompanyTabs = () => {};

const src = R('static/js/app.js');
const block = src.match(/window\.setContent = function setContent[\s\S]*?window\._renderPageToolbar = function[\s\S]*?\n\};/);
check('layout functions found in app.js', !!block);
eval(block[0]);

const contentToolbar = () => document.querySelector('#content .app-toolbar');
const corner = () => document.getElementById('topbar-actions');

// 1) a page like Contacts — actions hoist into the content toolbar, corner empties
window.setContent('<div class="cl-list">rows</div>');
window.setTopbarActions('<button class="btn btn-primary">+ New contact</button><button class="btn btn-glass">Columns</button>');
check('Corner is cleared of page actions', corner().children.length === 0, corner().innerHTML);
check('Create + secondary hoisted into content toolbar',
  !!contentToolbar() && /\+ New contact/.test(contentToolbar().textContent) && /Columns/.test(contentToolbar().textContent),
  contentToolbar() && contentToolbar().textContent);

// 2) a page like Work orders — the view switch STAYS in the corner, no hoist
window.App._pageToolbarHTML = '';
window.setContent('<div class="o-view">table</div>');
window.setTopbarActions('<div class="view-toggle"><button class="vt-btn">Table</button><button class="vt-btn">Board</button></div>');
check('View switch stays top-right', /Table/.test(corner().textContent) && corner().querySelector('.view-toggle'));
check('No content toolbar when only a view switch', !document.querySelector('#content .app-toolbar'));

// 3) call order independence — actions set BEFORE content still land in the toolbar
window.App._pageToolbarHTML = '';
corner().innerHTML = '';
window.setTopbarActions('<button class="btn btn-primary">+ Add asset</button>');
window.setContent('<div class="a-list">rows</div>');
check('Actions survive setContent regardless of call order',
  !!contentToolbar() && /\+ Add asset/.test(contentToolbar().textContent));

// static wiring
check('setContent re-renders the toolbar', /window\._renderPageToolbar\(\);/.test(src));
check('Navigation resets the stored toolbar', /App\._pageToolbarHTML = '';/.test(src));
check('Toolbar CSS present', /\.app-toolbar/.test(R('static/css/design.css')));
check('Cash money segment kept', /money-seg/.test(R('static/js/pages/cash.js')));
check('Build bumped', /build 144(b[w-z]|[c-z][a-z])/.test(R('index.html')));

const failed = results.filter(r => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
