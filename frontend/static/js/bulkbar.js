/* Build 144cw — reusable bottom-docked bulk-action bar.
   Pages own their selection Set; call BulkBar.show(count, actionsHTML, onClear)
   on change and BulkBar.hide() when empty. Matches the Orderry bulk-bar pattern
   (count + actions on the left, red destructive action on the right). */
const BulkBar = {
  show(count, actionsHTML, onClear) {
    if (!count) return this.hide();
    let bar = document.getElementById('bulk-bar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'bulk-bar'; bar.className = 'bulk-bar';
      document.body.appendChild(bar);
    }
    bar.innerHTML =
      `<button class="bb-x" title="Clear selection">✕</button>` +
      `<span class="bb-count">${count} selected</span>` +
      `<span class="bb-actions">${actionsHTML || ''}</span>`;
    bar.querySelector('.bb-x').onclick = () => { BulkBar.hide(); if (onClear) onClear(); };
    bar.style.display = 'flex';
  },
  hide() { const b = document.getElementById('bulk-bar'); if (b) b.remove(); },
};
window.BulkBar = BulkBar;
