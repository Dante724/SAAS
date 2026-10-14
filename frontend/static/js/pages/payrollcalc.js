/* ════════════════════════════════════════════════════════════
   Finance → Payroll calculation (build 144h)

   The month's compensation per employee — Base / Commission / Penalties /
   Bonuses / Total — with a per-employee drawer that itemizes the commissions,
   lets you add ad-hoc penalties and bonuses, and accrue the month.
   Mirrors Orderry's Finance → Payroll Calculation; hosted under Finance.
════════════════════════════════════════════════════════════ */
const PayrollCalcPage = {
  _embedBody: null,
  _month: null,        // 'YYYY-MM'
  _data: null,

  _thisMonth() { const d = new Date(); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`; },
  _shift(month, delta) {
    const [y, m] = month.split('-').map(Number);
    const d = new Date(y, m - 1 + delta, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
  },
  _monthLabel(month) {
    const [y, m] = month.split('-').map(Number);
    return new Date(y, m - 1, 1).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
  },
  _isCurrentOrFuture(month) { return month >= this._thisMonth(); },

  async mountInto(bodyId) {
    this._embedBody = bodyId;
    if (!this._month) this._month = this._thisMonth();
    await this.load();
  },

  _set(html) {
    const el = this._embedBody && document.getElementById(this._embedBody);
    if (el) el.innerHTML = html; else setContent(html);
  },

  async load() {
    this._set(`<div class="card" style="padding:26px;text-align:center;color:var(--tx-4)">Loading payroll…</div>`);
    try {
      this._data = await get(`/api/payroll/calculation?month=${this._month}`);
    } catch (e) {
      this._set(`<div class="card" style="padding:24px;color:var(--tx-4)">${e.message || 'Could not load payroll'}</div>`);
      return;
    }
    this._render();
  },

  go(delta) { this._month = this._shift(this._month, delta); this.load(); },

  _render() {
    const d = this._data, t = d.totals || {};
    const cur = this._isCurrentOrFuture(this._month);
    const rows = (d.items || []).map(r => {
      const accrued = r.accrual_status === 'accrued' || r.accrual_status === 'paid';
      return `<tr class="pc-row" onclick="PayrollCalcPage.openDetail('${r.employee_id}')">
        <td class="tbl-strong">${this._esc(r.name)}
          ${accrued ? `<span class="badge b-done" style="margin-left:6px">accrued</span>` : ''}</td>
        <td style="color:var(--tx-3)">${this._esc(r.position || '—')}</td>
        <td style="text-align:right">${fmt.currency(r.base_salary)}</td>
        <td style="text-align:right">${fmt.currency(r.commissions_total)}</td>
        <td style="text-align:right;color:${r.penalties_total ? 'var(--red)' : 'var(--tx-3)'}">${r.penalties_total ? '−' + fmt.currency(r.penalties_total) : fmt.currency(0)}</td>
        <td style="text-align:right;color:${r.bonuses_total ? 'var(--green)' : 'var(--tx-3)'}">${fmt.currency(r.bonuses_total)}</td>
        <td style="text-align:right;font-weight:900">${fmt.currency(r.computed_total)}</td>
      </tr>`;
    }).join('');

    this._set(`
      <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:14px;flex-wrap:wrap">
        <div style="display:flex;align-items:center;gap:8px">
          <button class="btn btn-glass btn-sm" onclick="PayrollCalcPage.go(-1)" title="Previous month">‹</button>
          <div style="font-weight:800;font-size:15px;min-width:150px;text-align:center">${this._monthLabel(this._month)}</div>
          <button class="btn btn-glass btn-sm" onclick="PayrollCalcPage.go(1)" title="Next month">›</button>
        </div>
        <div style="font-size:12px;color:var(--tx-4)">${cur ? 'Calculated so far — accrue once the month is over.' : 'Calculated, not yet accrued. Open an employee to accrue.'}</div>
      </div>
      <div class="card" style="padding:0">
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr>
            <th>Employee</th><th>Position</th>
            <th style="text-align:right">Base</th><th style="text-align:right">Commission</th>
            <th style="text-align:right">Penalties</th><th style="text-align:right">Bonuses</th>
            <th style="text-align:right">Total</th>
          </tr></thead>
          <tbody>${rows || `<tr><td colspan="7" style="text-align:center;color:var(--tx-4);padding:26px">No active employees.</td></tr>`}</tbody>
          ${(d.items || []).length ? `<tfoot><tr style="font-weight:900;border-top:2px solid var(--g-border)">
            <td colspan="2">Total — ${(d.items || []).length}</td>
            <td style="text-align:right">${fmt.currency(t.base_salary)}</td>
            <td style="text-align:right">${fmt.currency(t.commissions_total)}</td>
            <td style="text-align:right;color:${t.penalties_total ? 'var(--red)' : 'inherit'}">${t.penalties_total ? '−' + fmt.currency(t.penalties_total) : fmt.currency(0)}</td>
            <td style="text-align:right;color:${t.bonuses_total ? 'var(--green)' : 'inherit'}">${fmt.currency(t.bonuses_total)}</td>
            <td style="text-align:right">${fmt.currency(t.computed_total)}</td>
          </tr></tfoot>` : ''}
        </table></div>
      </div>`);
  },

  /* ── per-employee drawer ── */
  async openDetail(employeeId) {
    let d;
    try { d = await get(`/api/payroll/calculation/detail?employee_id=${employeeId}&month=${this._month}`); }
    catch (e) { return Toast.error(e.message); }
    this._detail = d;
    const row = (this._data.items || []).find(i => i.employee_id === employeeId) || {};
    const accrued = row.accrual_status === 'accrued' || row.accrual_status === 'paid';
    const cur = this._isCurrentOrFuture(this._month);

    const commRows = d.commissions.length ? d.commissions.map(c => `<tr>
      <td style="color:var(--tx-3);font-size:12px;white-space:nowrap">${fmt.date(c.date)}</td>
      <td>${this._esc(c.note || '—')}</td>
      <td style="text-align:right;font-variant-numeric:tabular-nums;color:${c.amount < 0 ? 'var(--red)' : 'inherit'}">${fmt.currency(c.amount)}</td>
    </tr>`).join('') : `<tr><td colspan="3" style="color:var(--tx-4);text-align:center;padding:14px">No commissions this month.</td></tr>`;

    const adjRows = (list, kind) => list.length ? list.map(a => `<tr>
      <td style="color:var(--tx-3);font-size:12px;white-space:nowrap">${fmt.date(a.date)}</td>
      <td>${this._esc(a.note || '—')}</td>
      <td style="text-align:right;font-variant-numeric:tabular-nums">${fmt.currency(Math.abs(a.amount))}</td>
      <td style="text-align:right"><button class="btn btn-glass btn-sm" onclick="PayrollCalcPage.removeAdj('${a.id}','${employeeId}')" title="Remove">×</button></td>
    </tr>`).join('') : `<tr><td colspan="4" style="color:var(--tx-4);text-align:center;padding:12px">No ${kind} this month.</td></tr>`;

    const chip = (label, val, color) => `<div class="detail-blob" style="min-width:0">
      <div class="d-label">${label}</div><div class="d-value" style="color:${color || 'var(--tx-1)'}">${val}</div></div>`;

    Modal.open(`
      <div class="modal-header"><span class="modal-title">${this._esc(d.name)} · ${this._monthLabel(this._month)}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="detail-grid" style="margin-bottom:16px">
          ${chip('Base', fmt.currency(d.base_salary))}
          ${chip('Commission', fmt.currency(d.commissions_total))}
          ${chip('Penalties', d.penalties_total ? '−' + fmt.currency(d.penalties_total) : fmt.currency(0), d.penalties_total ? 'var(--red)' : '')}
          ${chip('Bonuses', fmt.currency(d.bonuses_total), d.bonuses_total ? 'var(--green)' : '')}
          ${chip('Total', fmt.currency(d.computed_total))}
        </div>

        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:6px 0 6px">Commissions</div>
        <div class="tbl-wrap" style="max-height:220px;overflow:auto"><table class="tbl">
          <thead><tr><th>Date</th><th>Document / item</th><th style="text-align:right">Amount</th></tr></thead>
          <tbody>${commRows}</tbody></table></div>

        <div style="display:flex;align-items:center;justify-content:space-between;margin:16px 0 6px">
          <span style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em">Penalties</span>
          <button class="btn btn-glass btn-sm" onclick="PayrollCalcPage.adjModal('${employeeId}','penalty')">+ Penalty</button>
        </div>
        <div class="tbl-wrap"><table class="tbl"><tbody>${adjRows(d.penalties, 'penalties')}</tbody></table></div>

        <div style="display:flex;align-items:center;justify-content:space-between;margin:16px 0 6px">
          <span style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em">Bonuses</span>
          <button class="btn btn-glass btn-sm" onclick="PayrollCalcPage.adjModal('${employeeId}','bonus')">+ Bonus</button>
        </div>
        <div class="tbl-wrap"><table class="tbl"><tbody>${adjRows(d.bonuses, 'bonuses')}</tbody></table></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass btn-sm" onclick="openPrintPdf('/api/print/payroll-statement/${employeeId}','payroll.pdf')">Print</button>
        <button class="btn btn-glass" onclick="Modal.close()">Close</button>
        ${accrued
          ? `<button class="btn btn-glass" disabled>Accrued ✓</button>`
          : `<button class="btn btn-primary" ${cur ? 'disabled title="Wait until the month is over"' : ''} onclick="PayrollCalcPage.accrue('${employeeId}')">Create accrual</button>`}
      </div>`, 'modal-wide');
  },

  adjModal(employeeId, kind) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Add ${kind}</span>
        <button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Amount</label>
          <input class="field-input" id="adj-amt" type="number" min="0" step="0.01" placeholder="0.00"></div>
        <div class="field"><label class="field-label">Comment</label>
          <input class="field-input" id="adj-note" placeholder="${kind === 'penalty' ? 'e.g. late / rework' : 'e.g. great month'}"></div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="PayrollCalcPage.saveAdj('${employeeId}','${kind}')">Add ${kind}</button>
      </div>`, '', true);
  },

  async saveAdj(employeeId, kind) {
    const amount = parseFloat(document.getElementById('adj-amt').value);
    if (!amount || amount <= 0) return Toast.error('Enter a positive amount');
    const note = document.getElementById('adj-note').value || null;
    try {
      await post('/api/payroll/adjustment', { employee_id: employeeId, kind, amount, note, month: this._month });
      Modal.close();                         // close the small add modal
      Toast.success(`${kind[0].toUpperCase()}${kind.slice(1)} added`);
      await this.load();                     // refresh the table
      await this.openDetail(employeeId);     // re-open drawer with fresh data
    } catch (e) { Toast.error(e.message); }
  },

  async removeAdj(id, employeeId) {
    if (!await confirm2('Remove this entry?')) return;
    try {
      await del(`/api/payroll/adjustment/${id}`);
      Toast.success('Removed');
      await this.load();
      await this.openDetail(employeeId);
    } catch (e) { Toast.error(e.message); }
  },

  async accrue(employeeId) {
    if (!await confirm2(`Accrue ${this._monthLabel(this._month)} for this employee? This posts the base salary and locks the period.`)) return;
    try {
      await post('/api/payroll/calculation/accrue', { employee_id: employeeId, month: this._month });
      Modal.close();
      Toast.success('Payroll accrued');
      await this.load();
    } catch (e) { Toast.error(e.message); }
  },

  _esc(t) { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; },
};


/* ════════════════════════════════════════════════════════════
   Finance → Payroll accruals (build 144m)

   The read side of payroll: every accrual created from the calculation page,
   newest first, with base/commission/hourly/bonus/penalty/gross and status.
   An accrued run can be marked paid (posts the payout to the employee ledger).
════════════════════════════════════════════════════════════ */
const PayrollAccrualsPage = {
  _embedBody: null,
  _data: null,

  async mountInto(bodyId) { this._embedBody = bodyId; await this.load(); },
  _set(html) {
    const el = this._embedBody && document.getElementById(this._embedBody);
    if (el) el.innerHTML = html; else setContent(html);
  },

  async load() {
    this._set(`<div class="card" style="padding:26px;text-align:center;color:var(--tx-4)">Loading accruals…</div>`);
    try { this._data = await get('/api/payroll/runs'); }
    catch (e) { this._set(`<div class="card" style="padding:24px;color:var(--tx-4)">${e.message || 'Could not load accruals'}</div>`); return; }
    this._render();
  },

  _statusBadge(s) {
    if (s === 'paid') return '<span class="badge b-done">paid</span>';
    if (s === 'accrued') return '<span class="badge b-new">accrued</span>';
    return '<span class="badge b-muted">draft</span>';
  },
  _period(r) {
    const d = (s) => { const [y, m] = s.split('-').map(Number); return new Date(y, m - 1, 1).toLocaleDateString('en-US', { month: 'short', year: 'numeric' }); };
    return d(r.period_start);
  },

  _render() {
    const d = this._data, t = d.totals || {};
    const rows = (d.items || []).map(r => `<tr>
      <td class="tbl-strong">${this._esc(r.employee_name)}</td>
      <td style="color:var(--tx-3)">${this._period(r)}</td>
      <td style="text-align:right">${fmt.currency(r.base_salary)}</td>
      <td style="text-align:right">${fmt.currency(r.commissions_total)}</td>
      <td style="text-align:right">${fmt.currency(r.hourly_total)}</td>
      <td style="text-align:right;color:${r.bonuses_total ? 'var(--green)' : 'var(--tx-3)'}">${fmt.currency(r.bonuses_total)}</td>
      <td style="text-align:right;color:${r.penalties_total ? 'var(--red)' : 'var(--tx-3)'}">${r.penalties_total ? '−' + fmt.currency(r.penalties_total) : fmt.currency(0)}</td>
      <td style="text-align:right;font-weight:900">${fmt.currency(r.gross_total)}</td>
      <td>${this._statusBadge(r.status)}</td>
      <td style="text-align:right;white-space:nowrap">${r.status === 'accrued'
        ? `<button class="btn btn-success btn-sm" onclick="PayrollAccrualsPage.markPaid('${r.id}')">Mark paid</button>`
        : r.status === 'paid'
          ? `<span style="color:var(--tx-4);font-size:12px">${r.paid_at ? 'paid ' + fmt.date(r.paid_at) : 'paid'}</span>`
          : ''}</td>
    </tr>`).join('');

    this._set(`
      <div style="font-size:12.5px;color:var(--tx-3);margin-bottom:12px;max-width:760px">
        Accruals created from <b>Payroll calculation</b>. Each locks a month's pay for an employee onto their balance; marking one <b>paid</b> posts the payout.
      </div>
      <div class="card" style="padding:0">
        <div class="tbl-wrap"><table class="tbl">
          <thead><tr>
            <th>Employee</th><th>Period</th>
            <th style="text-align:right">Base</th><th style="text-align:right">Commission</th>
            <th style="text-align:right">Hourly</th><th style="text-align:right">Bonuses</th>
            <th style="text-align:right">Penalties</th><th style="text-align:right">Gross</th>
            <th>Status</th><th></th>
          </tr></thead>
          <tbody>${rows || `<tr><td colspan="10" style="text-align:center;color:var(--tx-4);padding:26px">No accruals yet. Create one from the Payroll calculation tab.</td></tr>`}</tbody>
          ${(d.items || []).length ? `<tfoot><tr style="font-weight:900;border-top:2px solid var(--g-border)">
            <td colspan="2">Total — ${(d.items || []).length}</td>
            <td style="text-align:right">${fmt.currency(t.base_salary)}</td>
            <td style="text-align:right">${fmt.currency(t.commissions_total)}</td>
            <td style="text-align:right">${fmt.currency(t.hourly_total)}</td>
            <td style="text-align:right">${fmt.currency(t.bonuses_total)}</td>
            <td style="text-align:right;color:${t.penalties_total ? 'var(--red)' : 'inherit'}">${t.penalties_total ? '−' + fmt.currency(t.penalties_total) : fmt.currency(0)}</td>
            <td style="text-align:right">${fmt.currency(t.gross_total)}</td>
            <td colspan="2"></td>
          </tr></tfoot>` : ''}
        </table></div>
      </div>`);
  },

  async markPaid(runId) {
    if (!await confirm2('Mark this accrual as paid? This posts the payout to the employee balance.')) return;
    try {
      await post(`/api/payroll/runs/${runId}/pay`, {});
      Toast.success('Marked paid');
      await this.load();
    } catch (e) { Toast.error(e.message); }
  },

  _esc(t) { const d = document.createElement('div'); d.textContent = t == null ? '' : String(t); return d.innerHTML; },
};
