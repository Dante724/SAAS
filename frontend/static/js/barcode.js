/* ════════════════════════════════════════════════════
   Barcode — the single app-wide barcode / QR helper (Build 144ef).
   One generator everywhere: Products, Services, Work orders, labels.
   The real Code128/QR SVG is rendered by the backend (same code the
   label/print engine uses) and fetched with the bearer token, so the
   preview on screen is byte-identical to what prints.

   Usage:
     Barcode.render('el-id', 'WO-0001', 'code128');   // inject real SVG
     const v = Barcode.value('SVC');                   // make a fresh value
════════════════════════════════════════════════════ */
const Barcode = {
  TYPES: ['code128', 'qr', 'ean13', 'ean8', 'upca'],

  // Generate a scannable value. code128/qr take any text; the numeric symbologies
  // (ean/upc) need digits, so we emit a digit run of the right length.
  value(prefix = '', type = 'code128') {
    const digits = (n) => Array.from({ length: n }, () => Math.floor(Math.random() * 10)).join('');
    if (type === 'ean13') return digits(13);
    if (type === 'ean8') return digits(8);
    if (type === 'upca') return digits(12);
    const p = (prefix || 'C').toString().toUpperCase().replace(/[^A-Z0-9]/g, '').slice(0, 4) || 'C';
    return `${p}-${digits(8)}`;
  },

  // Render the real SVG for `data` into the element. `type` 'qr' → QR, else Code128.
  // ean/upc values are drawn as Code128 of the same digits (still scannable, uniform look).
  async render(el, data, type = 'code128', opts = {}) {
    const node = typeof el === 'string' ? document.getElementById(el) : el;
    if (!node) return;
    const d = (data == null ? '' : String(data)).trim();
    if (!d) { node.innerHTML = `<span style="font-size:11.5px;color:var(--tx-4)">No code yet</span>`; return; }
    const kind = type === 'qr' ? 'qr' : 'code128';
    const h = opts.height ? `&height=${opts.height}` : '';
    try {
      const res = await fetch(`${API}/api/catalog/code.svg?type=${kind}&data=${encodeURIComponent(d)}${h}`,
        { headers: { Authorization: `Bearer ${Auth.token}` } });
      if (!res.ok) throw new Error(String(res.status));
      const svg = await res.text();
      node.innerHTML = `<div class="bc-svg" style="display:inline-block;max-width:100%">${svg}</div>`;
      const s = node.querySelector('svg'); if (s) { s.style.maxWidth = '100%'; s.style.height = 'auto'; }
    } catch (e) {
      node.innerHTML = `<span style="font-size:11.5px;color:var(--red)">Couldn't render code</span>`;
    }
  },
};
window.Barcode = Barcode;
