/* ════════════════════════════════════════════════════
   ColTools — build 144bm. Drag-to-resize table columns with the mouse,
   persisted per list in localStorage. Attach after a list renders:
     ColTools.resizable('wo-list-table', 'workflows');
   Generic: works on any <table> with a single header row.
════════════════════════════════════════════════════ */
const ColTools = {
  _key(k) { return 'ros_colw_' + k; },
  _load(k) { try { return JSON.parse(localStorage.getItem(this._key(k)) || '{}'); } catch (e) { return {}; } },
  _save(k, widths) { try { localStorage.setItem(this._key(k), JSON.stringify(widths)); } catch (e) {} },

  resizable(tableId, key) {
    const t = document.getElementById(tableId);
    if (!t || !t.tHead || !t.tHead.rows.length) return;
    if (t.dataset.rzKey === key && t.dataset.rzDone === '1') { /* re-render: re-apply */ }
    const ths = [...t.tHead.rows[0].cells];
    if (ths.length < 2) return;
    t.style.tableLayout = 'fixed';
    t.style.width = '100%';
    const saved = this._load(key);
    // Seed explicit widths from the current layout (once), so fixed layout doesn't jump.
    ths.forEach((th, i) => {
      const w = saved[i] || Math.round(th.getBoundingClientRect().width) || 90;
      th.style.width = w + 'px';
      th.style.position = th.style.position || 'relative';
      if (i < ths.length - 1 && !th.querySelector('.col-rz')) {
        const h = document.createElement('span');
        h.className = 'col-rz';
        h.addEventListener('pointerdown', (e) => this._start(e, th, ths, key));
        th.appendChild(h);
      }
    });
    t.dataset.rzKey = key;
    t.dataset.rzDone = '1';
  },

  _start(e, th, ths, key) {
    e.preventDefault();
    e.stopPropagation();
    const sx = e.clientX;
    const sw = th.getBoundingClientRect().width;
    document.body.classList.add('col-rzing');
    const move = (ev) => { th.style.width = Math.max(48, Math.round(sw + (ev.clientX - sx))) + 'px'; };
    const up = () => {
      document.removeEventListener('pointermove', move);
      document.removeEventListener('pointerup', up);
      document.body.classList.remove('col-rzing');
      const widths = {};
      ths.forEach((c, i) => { widths[i] = Math.round(c.getBoundingClientRect().width); });
      this._save(key, widths);
    };
    document.addEventListener('pointermove', move);
    document.addEventListener('pointerup', up);
  },
};
window.ColTools = ColTools;
