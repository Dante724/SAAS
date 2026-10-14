/* Build 144v — Label / price-tag printing, unified onto Document templates.

   Labels and price tags are now ordinary DocumentTemplates (doc_type label /
   price_tag / product_label / vehicle_label), authored in the same rich editor
   as every other document. This dialog just lets the inventory page pick one of
   those templates for the selected products and prints a tiled sheet — barcodes
   and QR are real, scannable vectors rendered server-side. */

const LABEL_PRINT_DOC_TYPES = ['label', 'price_tag', 'product_label', 'vehicle_label'];

/* Print dialog, callable from the inventory page for the selected products. */
const LabelPrint = {
  async open(partIds) {
    if (!partIds || !partIds.length) return Toast.error('Pick at least one product');
    let tpls = [];
    try {
      const all = (await get('/api/documents/templates')).items || [];
      tpls = all.filter(t => LABEL_PRINT_DOC_TYPES.includes(t.doc_type));
    } catch (e) { /* fall back to none */ }
    if (!tpls.length) {
      return Toast.error('No label templates yet — create one in Settings → Document templates (set its type to a label or price tag)');
    }
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Print labels</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <p style="color:var(--tx-3);font-size:13px;margin:0 0 12px">
          ${partIds.length} product(s). Each label is a document template — barcodes and QR print as scannable vectors.
        </p>
        <div class="field-row">
          <div class="field"><label class="field-label">Template</label>
            <select class="field-select" id="lp-tpl">
              ${tpls.map(t => `<option value="${t.id}" ${t.is_default ? 'selected' : ''}>${(t.name || 'Untitled').replace(/</g, '&lt;')}</option>`).join('')}
            </select></div>
          <div class="field"><label class="field-label">Copies of each</label>
            <input class="field-input" type="number" id="lp-copies" value="1" min="1" max="100"></div>
        </div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick='LabelPrint.go(${JSON.stringify(partIds)})'>Print</button>
      </div>`);
  },

  async go(partIds) {
    const body = {
      template_id: document.getElementById('lp-tpl').value,
      part_ids: partIds,
      copies: parseInt(document.getElementById('lp-copies').value, 10) || 1,
    };
    if (!body.template_id) return Toast.error('Choose a template');
    try {
      const r = await post('/api/documents/render-labels', body);
      const w = window.open('', '_blank');
      if (w) {
        w.document.write(`<html><head><title>${r.name}</title></head><body>${r.html}</body></html>`);
        w.document.close();
        w.focus();
        w.print();
      }
      Modal.close();
      Toast.success(`Prepared ${r.count} label(s)`);
    } catch (e) { Toast.error(e.message || 'Print failed'); }
  },
};
