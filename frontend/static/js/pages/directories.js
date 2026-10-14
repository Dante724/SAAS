/* Settings → Directories — org-customizable reference lists that feed dropdowns. */
const DirectoriesPage = {
  _sel: null,
  async load() {
    setTopbarActions(`<button class="btn btn-primary" onclick="DirectoriesPage.newDir()">+ New directory</button>`);
    setContent(`<section class="settings-pane"><div id="dir-root">Loading…</div></section>`);
    await this.render();
  },
  async render() {
    const root = document.getElementById('dir-root');
    let dirs = [];
    try { dirs = (await get('/api/directories')).items; } catch (e) { root.innerHTML = '<div class="empty">Failed to load.</div>'; return; }
    if (!this._sel && dirs.length) this._sel = dirs[0].id;
    let items = [];
    if (this._sel) { try { items = (await get(`/api/directories/${this._sel}/items`)).items; } catch (e) {} }
    const sel = dirs.find(d => d.id === this._sel);
    root.innerHTML = `
      <h2 style="margin:0 0 4px">Directories</h2>
      <p class="muted" style="margin:0 0 16px">Reusable lists that populate dropdowns across the app (asset types, manufacturers, reasons…).</p>
      <div style="display:grid;grid-template-columns:240px 1fr;gap:20px">
        <div class="card ind-col dir-list-panel" style="padding:10px">
          <div class="ind-col-head">Directories</div>
          ${dirs.map(d => `<div class="ind-row ${d.id===this._sel?'on':''}" onclick="DirectoriesPage.select('${d.id}')">
             <span>${d.name}${d.is_system?' <span class="ind-sys">system</span>':''}</span>
             ${d.is_system?'':`<button class="ind-x" onclick="event.stopPropagation();DirectoriesPage.delDir('${d.id}')">×</button>`}
           </div>`).join('') || '<div class="ind-hint">No directories.</div>'}
        </div>
        <div class="card glass-liquid" style="padding:22px">
          ${sel ? `
            <div class="ind-eyebrow">Directory</div>
            <div style="font-weight:800;font-size:18px;color:var(--tx-1);margin:2px 0 3px">${sel.name}</div>
            <div class="muted" style="font-size:12px;margin-bottom:16px">key: <code>${sel.key}</code></div>
            <div style="display:flex;gap:8px;margin-bottom:14px">
              <input class="field-input" id="dir-newitem" placeholder="Add a value…" style="flex:1"
                     onkeydown="if(event.key==='Enter')DirectoriesPage.addItem()">
              <button class="btn btn-primary btn-sm" onclick="DirectoriesPage.addItem()">Add</button>
            </div>
            <table class="data-table"><tbody>
              ${items.map(i => `<tr><td>${i.value}</td><td style="text-align:right;width:60px">
                <button class="btn btn-glass btn-sm" onclick="DirectoriesPage.delItem('${i.id}')">Remove</button></td></tr>`).join('')
                || '<tr><td class="empty">No values yet.</td></tr>'}
            </tbody></table>` : '<div class="ind-hint">Select a directory.</div>'}
        </div>
      </div>`;
  },
  select(id) { this._sel = id; this.render(); },
  async newDir() {
    const name = window.prompt('Directory name:'); if (!name) return;
    const key = window.prompt('Key (slug, e.g. warranty_provider):', name.toLowerCase().replace(/[^a-z0-9]+/g, '_')); if (!key) return;
    try { const d = await post('/api/directories', { key, name }); this._sel = d.id; Toast.success('Directory created'); this.render(); }
    catch (e) { Toast.error(e.message || 'Failed'); }
  },
  async delDir(id) {
    if (!(await confirm2('Delete this directory and its values?'))) return;
    try { await del(`/api/directories/${id}`); if (this._sel === id) this._sel = null; Toast.success('Deleted'); this.render(); }
    catch (e) { Toast.error(e.message || 'Failed'); }
  },
  async addItem() {
    const inp = document.getElementById('dir-newitem'); const value = inp.value.trim();
    if (!value || !this._sel) return;
    try { await post(`/api/directories/${this._sel}/items`, { value }); inp.value = ''; this.render(); }
    catch (e) { Toast.error(e.message || 'Failed'); }
  },
  async delItem(id) {
    try { await del(`/api/directories/items/${id}`); this.render(); }
    catch (e) { Toast.error(e.message || 'Failed'); }
  },
};
