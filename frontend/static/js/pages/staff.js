/* ════════════════════════════════════════════════════
   Staff — Employees + Roles (edit, login/password, salary)
   Modelled on Orderry Settings > Employees
════════════════════════════════════════════════════ */

// Build 131: the permission list is no longer hard-coded — it comes from the full
// server catalog (/api/settings/rbac/catalog, ~66 functions grouped by area).

const StaffPage = {
  _tab: 'employees',
  _roles: [],
  _employees: [],

  async load() {
    const label = this._tab === 'roles' ? 'Add role' : this._tab === 'payroll' ? 'New rule' : 'Add employee';
    setTopbarActions(`<button class="btn btn-primary" onclick="StaffPage.primaryAction()">+ ${label}</button>`);
    setContent(`
      <div class="rep-tabs" style="margin-bottom:18px">
        <button class="rep-tab ${this._tab==='employees'?'active':''}" onclick="StaffPage.setTab('employees')">Employees</button>
        <button class="rep-tab ${this._tab==='roles'?'active':''}" onclick="StaffPage.setTab('roles')">Roles &amp; permissions</button>
        <button class="rep-tab ${this._tab==='payroll'?'active':''}" onclick="StaffPage.setTab('payroll')">Payroll rules</button>
      </div>
      <div id="staff-body"></div>`);
    // Build 144g: payroll rules now live under Employees & roles.
    if (this._tab === 'payroll') { await PayrollSettingsPage.mountInto('staff-body'); return; }
    if (typeof PayrollSettingsPage !== 'undefined') PayrollSettingsPage._embedBody = null;
    await this.reload();
  },

  setTab(t) { this._tab = t; this.load(); },
  primaryAction() {
    if (this._tab === 'roles') this.roleModal();
    else if (this._tab === 'payroll') PayrollSettingsPage.ruleModal();
    else this.empModal();
  },

  async reload() {
    const body = document.getElementById('staff-body');
    if (!body) return;
    body.innerHTML = `<div class="card"><div class="tbl-wrap"><table class="tbl"><tbody>${skeletonRows(6)}</tbody></table></div></div>`;
    try {
      this._employees = await get('/api/staff/employees');
      this._roles = (await get('/api/settings/rbac/roles')).items;
      try { this._invites = (await get('/api/staff/invites')).items || []; } catch { this._invites = []; }
      if (!this._catalog) {
        try { this._catalog = await get('/api/settings/rbac/catalog'); }
        catch { this._catalog = { catalog: {}, templates: [] }; }
      }
      this._tab === 'roles' ? this._renderRoles() : this._renderEmployees();
    } catch (e) { Toast.error(e.message); }
  },

  _permTotal() {
    const cat = this._catalog?.catalog || {};
    return Object.values(cat).reduce((n, g) => n + g.length, 0);
  },
  _permGranted(perms) {
    // deny-wins: a code is granted unless explicitly false.
    const denied = Object.values(perms || {}).filter(v => v === false).length;
    return Math.max(this._permTotal() - denied, 0);
  },

  _roleName(id) { const r = this._roles.find(r => r.id === id); return r ? r.name : '—'; },

  _renderEmployees() {
    const body = document.getElementById('staff-body');
    const wageColors = { fixed:'var(--blue)', hourly:'var(--teal)', commission:'var(--purple)' };
    const invites = this._invites || [];
    const invitesCard = invites.length ? `<div class="card" style="margin-top:14px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <div style="font-weight:800;font-size:13px">Pending invitations</div>
        <div style="font-size:11.5px;color:var(--tx-4)">Awaiting the invitee to set a password</div></div>
      <div class="tbl-wrap"><table class="tbl">
        <thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Status</th><th></th></tr></thead>
        <tbody>${invites.map(i=>`<tr>
          <td style="font-weight:700">${i.email}</td>
          <td style="color:var(--tx-2)">${i.name||'—'}</td>
          <td>${this._roleName(i.role_id)}</td>
          <td>${i.expired?'<span class="badge" style="background:var(--red)1a;color:var(--red)">Expired</span>':'<span class="badge" style="background:var(--amber)1a;color:var(--amber)">Invited</span>'}</td>
          <td style="display:flex;gap:6px;justify-content:flex-end">
            <button class="btn btn-glass btn-sm" onclick="StaffPage.resendInvite('${i.email}','${i.name||''}','${i.role_id||''}')">Resend</button>
            <button class="btn btn-glass btn-sm" onclick="StaffPage.revokeInvite('${i.id}')">${icon('ban',13)}</button></td>
        </tr>`).join('')}</tbody>
      </table></div></div>` : '';
    body.innerHTML = `<div style="display:flex;justify-content:flex-end;margin-bottom:10px">
        <button class="btn btn-glass btn-sm" onclick="StaffPage.inviteModal()">${icon('user',13)} Invite teammate</button>
      </div>
      <div class="card"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Employee</th><th>Email (login)</th><th>Phone</th><th>Role</th><th>Salary</th></tr></thead>
      <tbody>${this._employees.map(e=>`<tr style="cursor:pointer" ondblclick="StaffPage.empModal('${e.id}')">
        <td><div style="display:flex;align-items:center;gap:10px">${avatarHtml(e.name,32)}<div style="font-weight:800">${e.name}</div></div></td>
        <td style="color:var(--tx-2);font-size:12.5px">${e.email}</td>
        <td style="color:var(--tx-2)">${e.phone||'—'}</td>
        <td>${this._roleName(e.role_id)}</td>
        <td><span class="badge" style="background:${wageColors[e.wage_type]||'var(--blue)'}1a;color:${wageColors[e.wage_type]||'var(--blue)'}">${e.wage_type} ${e.wage_value?fmt.currency(e.wage_value):''}</span></td>
      </tr>`).join('')}</tbody>
    </table></div></div>${invitesCard}`;
  },

  /* ── Build 144cc: teammate invitations ── */
  inviteModal(prefill = {}) {
    const roleOpts = (this._roles || []).map(r => `<option value="${r.id}" ${prefill.role_id===r.id?'selected':''}>${r.name}</option>`).join('');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Invite a teammate</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Email</label>
          <input class="field-input" id="iv-m-email" type="email" value="${prefill.email||''}" placeholder="teammate@shop.com"></div>
        <div class="field"><label class="field-label">Name (optional)</label>
          <input class="field-input" id="iv-m-name" type="text" value="${prefill.name||''}" placeholder="Jane Technician"></div>
        <div class="field"><label class="field-label">Role</label>
          <select class="field-input" id="iv-m-role"><option value="">No role (owner-level)</option>${roleOpts}</select></div>
        <div style="font-size:11.5px;color:var(--tx-4)">They'll get an email link to set their own password. The invite expires in 7 days.</div>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="StaffPage.submitInvite()">Send invitation</button>
      </div>`, 'modal-narrow');
  },

  async submitInvite() {
    const email = (document.getElementById('iv-m-email').value||'').trim();
    if (!email || !email.includes('@')) return Toast.error('Enter a valid email');
    const body = { email, name: document.getElementById('iv-m-name').value||null, role_id: document.getElementById('iv-m-role').value||null };
    try { await post('/api/staff/invites', body); Toast.success('Invitation sent'); Modal.close(); this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  async resendInvite(email, name, roleId) {
    try { await post('/api/staff/invites', { email, name: name||null, role_id: roleId||null }); Toast.success('Invitation re-sent'); this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  async revokeInvite(id) {
    try { await del(`/api/staff/invites/${id}`); Toast.success('Invitation revoked'); this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  _renderRoles() {
    const body = document.getElementById('staff-body');
    body.innerHTML = `<div class="card"><div class="tbl-wrap"><table class="tbl">
      <thead><tr><th>Role</th><th>Employees</th><th>Permissions granted</th></tr></thead>
      <tbody>${this._roles.length ? this._roles.map(r=>`<tr style="cursor:pointer" ondblclick="StaffPage.roleModal('${r.id}')">
        <td style="font-weight:800">${r.name}</td>
        <td>${this._employees.filter(e=>e.role_id===r.id).length}</td>
        <td style="color:var(--tx-3);font-size:12px">${this._permGranted(r.permissions)} of ${this._permTotal()}</td>
      </tr>`).join('') : emptyRow(3,'users','No roles yet','Create a role to group permissions')}</tbody>
    </table></div></div>`;
  },

  /* ── Employee profile ── */
  async empProfile(id) {
    const e = this._employees.find(x => x.id === id);
    if (!e) return;
    Modal.open(`
      <div class="modal-header">
        <div style="display:flex;align-items:center;gap:12px">${avatarHtml(e.name,36)}
          <div><span class="modal-title">${e.name}</span>${e.position?`<div style="font-size:12px;color:var(--tx-3);font-weight:600">${e.position}</div>`:''}</div>
        </div>
        <div style="display:flex;align-items:center;gap:8px">
          <button class="btn btn-primary btn-sm" onclick="StaffPage.empModal('${e.id}')">${icon('bolt',13)} Edit profile</button>
          <button class="modal-close" onclick="Modal.close()">×</button>
        </div>
      </div>
      <div class="modal-body">
        <div class="detail-grid" style="margin-bottom:16px">
          <div class="detail-blob"><div class="d-label">Login email</div><div class="d-value" style="font-size:12.5px">${e.email}</div></div>
          <div class="detail-blob"><div class="d-label">Phone</div><div class="d-value">${e.phone||'—'}</div></div>
          <div class="detail-blob"><div class="d-label">Position</div><div class="d-value">${e.position||'—'}</div></div>
          <div class="detail-blob"><div class="d-label">Hire date</div><div class="d-value">${e.hired_at?fmt.date(e.hired_at):'—'}</div></div>
          <div class="detail-blob"><div class="d-label">Role (access)</div><div class="d-value">${this._roleName(e.role_id)}</div></div>
          <div class="detail-blob"><div class="d-label">Salary</div><div class="d-value">${e.wage_type} ${e.wage_value?'· '+fmt.currency(e.wage_value):''}</div></div>
          <div class="detail-blob"><div class="d-label">Labour commission</div><div class="d-value">${e.commission_labour||0}%</div></div>
          <div class="detail-blob"><div class="d-label">Parts commission</div><div class="d-value">${e.commission_parts||0}%</div></div>
          <div class="detail-blob"><div class="d-label">Weekly hours</div><div class="d-value">${e.weekly_hours!=null?e.weekly_hours+' h/wk':'—'}</div></div>
          <div class="detail-blob"><div class="d-label">Hourly pay by</div><div class="d-value">${(e.hourly_basis||'labour')==='attendance'?'Shop attendance':'Labour hours'}</div></div>
          <div class="detail-blob"><div class="d-label">Clocks shown</div><div class="d-value" style="font-size:12.5px">${[e.track_job_hours!==false?'Work-order hours':null,e.track_attendance!==false?'Attendance':null].filter(Boolean).join(' · ')||'None'}</div></div>
          <div class="detail-blob"><div class="d-label">Employee ITN</div><div class="d-value" style="font-size:12.5px">${e.tax_id||'—'}</div></div>
          <div class="detail-blob"><div class="d-label">Public-document name</div><div class="d-value" style="font-size:12.5px">${e.public_name||'—'}</div></div>
        </div>
        ${e.note?`<div class="set-section" style="padding:12px 14px"><div class="d-label" style="margin-bottom:4px">Note</div><div style="font-size:13px;color:var(--tx-2);white-space:pre-wrap">${(e.note||'').replace(/</g,'&lt;')}</div></div>`:''}
      </div>
      <div class="modal-footer">
        <button class="btn btn-danger btn-sm" onclick="StaffPage.remove('${e.id}','${e.name.replace(/'/g,"\\'")}')">Deactivate</button>
        <button class="btn btn-glass btn-sm" onclick="openPrintPdf('/api/print/payroll-statement/${e.id}','payroll.pdf')">Payroll statement</button>
        <button class="btn btn-glass btn-sm" onclick="StaffPage.passwordModal('${e.id}')">Set password</button>
        <button class="btn btn-glass btn-sm" onclick="StaffPage.resetMfa('${e.id}','${e.name.replace(/'/g,"\\'")}')">Reset 2FA</button>
        <button class="btn btn-primary" onclick="StaffPage.empModal('${e.id}')">Edit</button>
      </div>`, 'modal-wide');
  },

  empModal(id = null) {
    const e = id ? this._employees.find(x => x.id === id) : null;
    const roleOpts = `<option value="">No role</option>` + this._roles.map(r => `<option value="${r.id}" ${e?.role_id===r.id?'selected':''}>${r.name}</option>`).join('');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${e?'Edit employee':'Add employee'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field-row">
          <div class="field"><label class="field-label">Full name *</label><input class="field-input" id="ne-name" value="${(e?.name||'').replace(/"/g,'&quot;')}" placeholder="Priya Patel"></div>
          <div class="field"><label class="field-label">Email (login) *</label><input class="field-input" id="ne-email" type="email" value="${e?.email||''}" placeholder="priya@shop.com"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Phone</label><input class="field-input" id="ne-phone" value="${e?.phone||''}" placeholder="+91-..."></div>
          <div class="field"><label class="field-label">Role (access)</label><select class="field-select" id="ne-role">${roleOpts}</select></div>
        </div>
        ${!e ? `<div class="field"><label class="field-label">Initial password</label><input class="field-input" id="ne-pass" type="text" value="changeme123"></div>` : ''}
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 8px">General info</div>
        <div class="field-row">
          <div class="field"><label class="field-label">Position (job title)</label><input class="field-input" id="ne-position" value="${(e?.position||'').replace(/"/g,'&quot;')}" placeholder="e.g. Technician"></div>
          <div class="field"><label class="field-label">Hire date</label><input class="field-input" id="ne-hired" type="date" value="${e?.hired_at||''}"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Employee ITN / tax number</label><input class="field-input" id="ne-taxid" value="${(e?.tax_id||'').replace(/"/g,'&quot;')}"></div>
          <div class="field"><label class="field-label">Name for public documents</label><input class="field-input" id="ne-pubname" value="${(e?.public_name||'').replace(/"/g,'&quot;')}" placeholder="Shown to customers"></div>
        </div>
        <div class="field"><label class="field-label">Note</label><textarea class="field-textarea" id="ne-note" rows="2" placeholder="Internal note about this employee">${e?.note||''}</textarea></div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 8px">Salary &amp; commission</div>
        <div class="field-row">
          <div class="field"><label class="field-label">Base salary type</label>
            <select class="field-select" id="ne-wtype">${[['fixed','Monthly fixed'],['hourly','Hourly'],['commission','Commission only']].map(([k,l])=>`<option value="${k}" ${e?.wage_type===k?'selected':''}>${l}</option>`).join('')}</select>
          </div>
          <div class="field"><label class="field-label">Base amount</label><input class="field-input" id="ne-wval" type="number" min="0" value="${e?.wage_value||0}"></div>
        </div>
        <div class="field-row">
          <div class="field"><label class="field-label">Labour commission %</label><input class="field-input" id="ne-clab" type="number" min="0" value="${e?.commission_labour||0}"></div>
          <div class="field"><label class="field-label">Parts commission %</label><input class="field-input" id="ne-cpart" type="number" min="0" value="${e?.commission_parts||0}"></div>
        </div>
        <div class="field"><label class="field-label">Hourly pay based on</label>
          <select class="field-select" id="ne-hbasis">
            ${[['labour','Labour hours — time on work orders'],['attendance','Shop attendance — clock-in / clock-out']]
              .map(([k,l])=>`<option value="${k}" ${((e?.hourly_basis)||'labour')===k?'selected':''}>${l}</option>`).join('')}
          </select>
          <div style="font-size:11px;color:var(--tx-4);margin-top:4px">Which clock pays this person by the hour. Only applies when the salary type is <b>Hourly</b>.</div>
        </div>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 8px">Time tracking</div>
        <div style="font-size:11.5px;color:var(--tx-4);margin-bottom:8px">Choose which clocks this employee sees in the time-clock widget.</div>
        <label class="cash-check"><input type="checkbox" id="ne-track-job" ${(e?e.track_job_hours!==false:true)?'checked':''}> Track work-order hours (job / labour timer)</label>
        <label class="cash-check"><input type="checkbox" id="ne-track-att" ${(e?e.track_attendance!==false:true)?'checked':''}> Track shop attendance (clock-in / clock-out)</label>
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:14px 0 8px">Working hours</div>
        <div class="field"><label class="field-label">Expected hours / week</label><input class="field-input" id="ne-whrs" type="number" min="0" step="0.5" value="${e?.weekly_hours!=null?e.weekly_hours:''}" placeholder="e.g. 40"></div>
      </div>
      <div class="modal-footer" style="display:flex;justify-content:space-between;align-items:center">
        <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
          <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
          <button class="btn btn-primary" onclick="StaffPage.submitEmp(${e?`'${e.id}'`:'null'})">${e?'Save changes':'Add employee'}</button>
          ${e?`<button class="btn btn-glass btn-sm" onclick="StaffPage.passwordModal('${e.id}')">Set password</button>
          <button class="btn btn-glass btn-sm" onclick="StaffPage.resetMfa('${e.id}','${e.name.replace(/'/g,"\\'")}')">Reset 2FA</button>
          <button class="btn btn-glass btn-sm" onclick="openPrintPdf('/api/print/payroll-statement/${e.id}','payroll.pdf')">Payroll statement</button>`:''}
        </div>
        ${e?`<button class="btn btn-danger btn-sm" onclick="StaffPage.remove('${e.id}','${e.name.replace(/'/g,"\\'")}')">Deactivate</button>`:''}
      </div>`, 'modal-wide');
  },

  async submitEmp(id) {
    const name = document.getElementById('ne-name').value.trim();
    const email = document.getElementById('ne-email').value.trim();
    if (!name || !email) return Toast.error('Name and email are required');
    const body = {
      name, email, phone: document.getElementById('ne-phone').value||null,
      role_id: document.getElementById('ne-role').value||null,
      wage_type: document.getElementById('ne-wtype').value,
      wage_value: parseFloat(document.getElementById('ne-wval').value)||0,
      commission_labour: parseFloat(document.getElementById('ne-clab').value)||0,
      commission_parts: parseFloat(document.getElementById('ne-cpart').value)||0,
      weekly_hours: document.getElementById('ne-whrs').value!==''?parseFloat(document.getElementById('ne-whrs').value):null,
      hourly_basis: document.getElementById('ne-hbasis').value,
      track_job_hours: document.getElementById('ne-track-job').checked,
      track_attendance: document.getElementById('ne-track-att').checked,
      position: document.getElementById('ne-position').value||null,
      hired_at: document.getElementById('ne-hired').value||null,
      tax_id: document.getElementById('ne-taxid').value||null,
      public_name: document.getElementById('ne-pubname').value||null,
      note: document.getElementById('ne-note').value||null,
    };
    try {
      if (id) { await patch(`/api/staff/employees/${id}`, body); Toast.success('Employee updated'); }
      else { body.password = document.getElementById('ne-pass').value||'changeme123'; await post('/api/staff/employees', body); Toast.success('Employee added'); }
      Modal.close(); this.reload();
    } catch (e) { Toast.error(e.message); }
  },

  passwordModal(id) {
    Modal.open(`
      <div class="modal-header"><span class="modal-title">Password</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Set a password directly</label><input class="field-input" id="pw-new" type="text" placeholder="At least 6 characters"></div>
        <div style="font-size:11.5px;color:var(--tx-4)">The employee logs in with their email and this password.</div>
        <div style="border-top:1px solid var(--line);margin:16px 0 12px"></div>
        <div style="font-size:12.5px;color:var(--tx-3);margin-bottom:8px">Prefer to let them choose their own? Email a secure reset link instead — it expires in 1 hour.</div>
        <button class="btn btn-glass btn-sm" onclick="StaffPage.sendReset('${id}')">${icon('message',13)} Email a reset link</button>
      </div>
      <div class="modal-footer">
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="StaffPage.submitPassword('${id}')">Set password</button>
      </div>`, 'modal-narrow');
  },

  async submitPassword(id) {
    const password = document.getElementById('pw-new').value;
    if (!password || password.length < 6) return Toast.error('At least 6 characters');
    try { await post(`/api/staff/employees/${id}/password`, { password }); Toast.success('Password set'); Modal.close(); }
    catch (e) { Toast.error(e.message); }
  },

  async sendReset(id) {
    try { await post(`/api/staff/employees/${id}/send-reset`, {}); Toast.success('Reset link emailed'); Modal.close(); }
    catch (e) { Toast.error(e.message); }
  },

  async resetMfa(id, name) {
    const ok = await confirm2(`Reset two-factor authentication for ${name}? They'll be signed out and asked to set it up again next time they log in.`);
    if (!ok) return;
    try { await post(`/api/staff/employees/${id}/reset-mfa`, {}); Toast.success('2FA reset'); Modal.close(); }
    catch (e) { Toast.error(e.message); }
  },

  async remove(id, name) {
    const ok = await confirm2(`Deactivate ${name}? They will no longer be able to log in.`);
    if (!ok) return;
    try { await del(`/api/staff/employees/${id}`); Toast.success('Employee deactivated'); Modal.close(); this.reload(); }
    catch (e) { Toast.error(e.message); }
  },

  /* ── Roles ── */
  roleModal(id = null) {
    const r = id ? this._roles.find(x => x.id === id) : null;
    const perms = r?.permissions || {};
    const cat = this._catalog?.catalog || {};
    // deny-wins: a code is allowed unless explicitly false.
    const allowed = (k) => perms[k] !== false;
    const groupHtml = Object.entries(cat).map(([group, list]) => {
      const gid = 'g_' + group.replace(/[^a-z0-9]/gi, '_');
      return `<div class="set-section perm-group" data-group="${group.toLowerCase()}" style="margin:0 0 12px;padding:12px 14px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
          <div class="ss-head" style="margin:0">${group}</div>
          <div style="display:flex;gap:8px">
            <button type="button" class="btn btn-glass btn-sm" onclick="StaffPage._groupSet('${gid}',true)">All</button>
            <button type="button" class="btn btn-glass btn-sm" onclick="StaffPage._groupSet('${gid}',false)">None</button>
          </div>
        </div>
        <div id="${gid}" style="display:grid;grid-template-columns:1fr 1fr;gap:2px 18px">
          ${list.map(p => `<label class="cash-check perm-row" data-label="${(p.label||p.code).toLowerCase()}" style="margin-bottom:5px">
            <input type="checkbox" data-permcode="${p.code}" ${allowed(p.code) ? 'checked' : ''}> ${p.label || p.code}</label>`).join('')}
        </div>
      </div>`;
    }).join('');
    Modal.open(`
      <div class="modal-header"><span class="modal-title">${r?'Edit role':'New role'}</span><button class="modal-close" onclick="Modal.close()">×</button></div>
      <div class="modal-body">
        <div class="field"><label class="field-label">Role name *</label><input class="field-input" id="rl-name" value="${(r?.name||'').replace(/"/g,'&quot;')}" placeholder="Technician, Manager, Cashier…"></div>
        <div style="display:flex;align-items:center;justify-content:space-between;margin:14px 0 8px">
          <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em">Permissions — ${this._permTotal()} functions</div>
          <input class="field-input" id="rl-search" placeholder="Filter permissions…" style="max-width:220px;padding:7px 12px" oninput="StaffPage._filterPerms(this.value)">
        </div>
        <div style="font-size:11.5px;color:var(--tx-4);margin-bottom:10px">Every function of the app is a toggle. Unchecking denies it for anyone in this role (deny wins).</div>
        ${groupHtml || '<div style="color:var(--tx-4);font-size:13px;padding:10px">Permission catalog unavailable.</div>'}
        <div style="font-size:10.5px;font-weight:800;color:var(--tx-3);text-transform:uppercase;letter-spacing:.06em;margin:18px 0 6px">Access conditions</div>
        <p style="color:var(--tx-4);font-size:12px;margin:0 0 10px">Permissions decide <em>what</em> someone can do; these decide <em>which records</em> they can see. "Own" still lets them attach any client to an order — it only limits browsing.</p>
        <div class="field-row">
          <div class="field"><label class="field-label">Contacts</label>
            <select class="field-select" id="rl-contact">
              <option value="all" ${(r?.contact_scope||'all')==='all'?'selected':''}>All contacts</option>
              <option value="own" ${r?.contact_scope==='own'?'selected':''}>Only contacts they manage</option>
            </select></div>
          <div class="field"><label class="field-label">Work orders</label>
            <select class="field-select" id="rl-order">
              <option value="all" ${(r?.order_scope||'all')==='all'?'selected':''}>All work orders</option>
              <option value="own" ${r?.order_scope==='own'?'selected':''}>Only orders assigned to them</option>
            </select></div>
        </div>
        <div class="field">
          <label class="field-label">Own-compensation visibility (days)</label>
          <input class="field-input" type="number" id="rl-payvis" min="0"
                 value="${r?.payroll_visibility_days ?? ''}" placeholder="Blank = no limit">
        </div>
      </div>
      <div class="modal-footer">
        ${r?`<button class="btn btn-danger btn-sm" onclick="StaffPage.delRole('${r.id}')">Delete</button>`:''}
        <button class="btn btn-glass" onclick="Modal.close()">Cancel</button>
        <button class="btn btn-primary" onclick="StaffPage.submitRole(${r?`'${r.id}'`:'null'})">${r?'Save':'Create role'}</button>
      </div>`, 'modal-wide');
  },

  _groupSet(gid, on) {
    const box = document.getElementById(gid);
    if (box) box.querySelectorAll('[data-permcode]').forEach(cb => { cb.checked = on; });
  },
  _filterPerms(q) {
    const s = (q || '').trim().toLowerCase();
    document.querySelectorAll('.perm-row').forEach(row => {
      row.style.display = (!s || (row.dataset.label || '').includes(s)) ? '' : 'none';
    });
    document.querySelectorAll('.perm-group').forEach(g => {
      const anyVisible = Array.from(g.querySelectorAll('.perm-row')).some(r => r.style.display !== 'none');
      g.style.display = anyVisible ? '' : 'none';
    });
  },

  async submitRole(id) {
    const name = document.getElementById('rl-name').value.trim();
    if (!name) return Toast.error('Name is required');
    const permissions = {};
    document.querySelectorAll('[data-permcode]').forEach(cb => { permissions[cb.dataset.permcode] = cb.checked; });
    const payvis = document.getElementById('rl-payvis').value;
    const body = {
      name, permissions,
      contact_scope: document.getElementById('rl-contact').value,
      order_scope: document.getElementById('rl-order').value,
      payroll_visibility_days: payvis === '' ? null : (parseInt(payvis, 10) || 0),
    };
    try {
      if (id) { await put(`/api/settings/rbac/roles/${id}`, body); Toast.success('Role updated'); }
      else { await post('/api/settings/rbac/roles', body); Toast.success('Role created'); }
      Modal.close(); this.reload();
      // Build 144L: re-apply the signed-in user's permissions so a denied feature
      // disappears now (if they edited their own role), not only after a reload.
      if (typeof App !== 'undefined' && App.refreshPerms) App.refreshPerms();
    } catch (e) { Toast.error(e.message); }
  },

  async delRole(id) {
    try {
      await del(`/api/settings/rbac/roles/${id}`); Toast.success('Role deleted'); Modal.close(); this.reload();
      if (typeof App !== 'undefined' && App.refreshPerms) App.refreshPerms();
    } catch (e) { Toast.error(e.message); }
  },
};

Router.register('staff', () => StaffPage.load());
