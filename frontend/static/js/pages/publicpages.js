/* Settings → Public pages — online booking appearance + settings,
   and info on the customer order/estimate tracking page. */
const PublicPagesPage = {
  _tab: 'booking', _cfg: null,

  async load() {
    setTopbarActions('');
    try { this._cfg = await get('/api/staff/public-pages'); }
    catch (e) { return Toast.error(e.message); }
    this._render();
  },

  setTab(t) { this._tab = t; this._render(); },

  _bookingUrl() {
    return `${location.origin}/book/${this._cfg.slug}`;
  },

  _render() {
    setContent(`
      <div class="set-hero">
        <div>
          <h1>Public pages</h1>
          <p>Customer-facing pages: the online booking page clients use to request appointments, and the tracking page they open to follow an order or approve an estimate.</p>
        </div>
        <div class="seg">
          <button class="${this._tab==='booking'?'on':''}" onclick="PublicPagesPage.setTab('booking')">Online booking</button>
          <button class="${this._tab==='order'?'on':''}" onclick="PublicPagesPage.setTab('order')">Order / Estimate</button>
        </div>
      </div>
      <div id="pp-body"></div>`);
    this._tab === 'booking' ? this._renderBooking() : this._renderOrder();
  },

  _renderBooking() {
    const c = this._cfg;
    document.getElementById('pp-body').innerHTML = `
      <div class="pp-card">
        <div class="pp-eyebrow">Your page URL</div>
        <div class="pp-urlbar">
          <input class="field-input" id="pp-url" readonly value="${this._bookingUrl()}" style="flex:1;font-family:monospace;font-size:12.5px;border:none;background:transparent">
          <button class="btn btn-glass btn-sm" onclick="PublicPagesPage.copyUrl()">Copy</button>
          <button class="btn btn-glass btn-sm" onclick="window.open(PublicPagesPage._bookingUrl(),'_blank')">Open</button>
          <button class="btn btn-glass btn-sm" onclick="PublicPagesPage.changeSubdomain()">Change subdomain</button>
        </div>
        <div class="pp-hint">Share this link with clients to let them book appointments online.</div>

        <label class="cash-check" style="margin-top:16px"><input type="checkbox" id="pp-enabled" ${c.booking_enabled?'checked':''}> Online booking enabled</label>

        <div class="pp-grid">
          <div class="pp-sub">
            <div class="pp-sub-title">Appearance</div>
            <div class="field-row">
              <div class="field"><label class="field-label">Accent color</label><input class="field-input" type="color" id="pp-color" value="${c.page_color||'#3b6fe0'}" style="height:42px;padding:4px"></div>
              <div class="field"><label class="field-label">Border radius</label>
                <select class="field-select" id="pp-radius">
                  <option value="4" ${c.page_radius==4?'selected':''}>Sharp — 4px</option>
                  <option value="8" ${c.page_radius==8?'selected':''}>Standard — 8px</option>
                  <option value="14" ${c.page_radius==14?'selected':''}>Rounded — 14px</option>
                </select></div>
            </div>
            <div class="field"><label class="field-label">Background pattern</label>
              <select class="field-select" id="pp-bg">
                ${['none','topo','gears','dots','waves'].map(p=>`<option value="${p}" ${(c.page_background_pattern||'none')===p?'selected':''}>${p[0].toUpperCase()+p.slice(1)}</option>`).join('')}
              </select></div>
            <label class="cash-check"><input type="checkbox" id="pp-lang" ${c.page_show_language?'checked':''}> Show language switcher</label>
            <label class="cash-check"><input type="checkbox" id="pp-logo" ${c.page_show_logo?'checked':''}> Display logo</label>
          </div>

          <div class="pp-sub">
            <div class="pp-sub-title">Booking settings</div>
            <div class="field-row">
              <div class="field"><label class="field-label">Time slot</label>
                <select class="field-select" id="pp-slot">
                  ${[15,20,30,45,60,90,120].map(m=>`<option value="${m}" ${c.booking_time_slot==m?'selected':''}>${m} min</option>`).join('')}
                </select></div>
              <div class="field"><label class="field-label">Earliest booking (lead time)</label>
                <select class="field-select" id="pp-lead">
                  ${[[0,'No limit'],[60,'1 hour ahead'],[120,'2 hours ahead'],[240,'4 hours ahead'],[1440,'1 day ahead'],[2880,'2 days ahead']].map(([m,lb])=>`<option value="${m}" ${(c.booking_lead_min||0)==m?'selected':''}>${lb}</option>`).join('')}
                </select></div>
              <div class="field"><label class="field-label">Book up to</label>
                <select class="field-select" id="pp-window">
                  ${[[7,'1 week ahead'],[14,'2 weeks ahead'],[30,'1 month ahead'],[60,'2 months ahead'],[90,'3 months ahead']].map(([d,lb])=>`<option value="${d}" ${(c.booking_window_days||30)==d?'selected':''}>${lb}</option>`).join('')}
                </select></div>
            </div>
            <label class="cash-check"><input type="checkbox" id="pp-multi" ${c.booking_allow_multiple?'checked':''}> Allow the same time to be booked more than once (double-booking)</label>
            <label class="cash-check"><input type="checkbox" id="pp-orders" ${c.booking_consider_orders?'checked':''}> Consider work orders when calculating available time slots</label>
            <div style="font-size:11.5px;color:var(--tx-4);margin-top:6px">Slots are offered only inside each location's working hours (Settings → Locations).</div>
            <div class="field-label" style="margin-top:14px">Booking page theme</div>
            <div class="pp-themes" id="pp-themes">
              ${[[1,'Premium glass','linear-gradient(120deg,#dfe8ff,#f7f4ff)','#3d63ff'],
                 [2,'Bold minimal','#0c0d10','#c8ff3d'],
                 [3,'Warm friendly','#fff7ed','#f0871e'],
                 [4,'Editorial','#faf8f3','#1c1a17']].map(([n,lb,bg,ac])=>`
                <div class="pp-theme ${(c.booking_theme||1)==n?'on':''}" data-theme="${n}" onclick="PublicPagesPage.pickTheme(${n})">
                  <div class="pp-theme-sw" style="background:${bg}"><span style="background:${ac}"></span></div>
                  <div class="pp-theme-lb">${lb}</div>
                </div>`).join('')}
            </div>
          </div>
        </div>

        <div class="pp-foot">
          <button class="btn btn-primary" onclick="PublicPagesPage.save()">Save changes</button>
        </div>
      </div>`;
  },

  _renderOrder() {
    const c = this._cfg || {};
    document.getElementById('pp-body').innerHTML = `
      <div class="pp-card">
        <div class="pp-sub-title" style="font-size:15px">Order / Estimate page</div>
        <div class="pp-hint" style="margin-top:2px;max-width:60ch">
          Public order/estimate pages let clients see status and approve estimates with an e-signature.
          Choose which company details and price rows appear.
        </div>

        <div class="pp-grid">
          <div class="pp-sub">
            <div class="pp-sub-title">Header</div>
            <label class="cash-check"><input type="checkbox" id="oe-logo" ${c.oe_show_logo!==false?'checked':''}> Show company logo</label>
            <div class="field" style="max-width:200px;margin:12px 0 4px">
              <label class="field-label">Logo size</label>
              <select class="field-select" id="oe-logo-size">
                ${['small','big'].map(v=>`<option value="${v}" ${c.oe_logo_size===v?'selected':''}>${v[0].toUpperCase()+v.slice(1)}</option>`).join('')}
              </select></div>
            <label class="cash-check"><input type="checkbox" id="oe-name" ${c.oe_show_name!==false?'checked':''}> Show company name</label>
            <label class="cash-check"><input type="checkbox" id="oe-addr" ${c.oe_show_address!==false?'checked':''}> Show company address</label>
            <label class="cash-check"><input type="checkbox" id="oe-email" ${c.oe_show_email!==false?'checked':''}> Show company email</label>
            <label class="cash-check"><input type="checkbox" id="oe-desc" ${c.oe_show_description!==false?'checked':''}> Show company description</label>
          </div>

          <div class="pp-sub">
            <div class="pp-sub-title">Products &amp; services</div>
            <label class="cash-check"><input type="checkbox" id="oe-sep" ${c.oe_separate_tables?'checked':''}> Show products and services in separate tables</label>
            <label class="cash-check"><input type="checkbox" id="oe-zp" ${c.oe_show_zero_price_products!==false?'checked':''}> Show products with a zero price</label>
            <label class="cash-check"><input type="checkbox" id="oe-zs" ${c.oe_show_zero_price_services!==false?'checked':''}> Show services with a zero price</label>
          </div>

          <div class="pp-sub">
            <div class="pp-sub-title">Customer actions</div>
            <label class="cash-check"><input type="checkbox" id="oe-approval" ${c.oe_allow_approval!==false?'checked':''}> Let customers approve / decline estimates online</label>
            <label class="cash-check"><input type="checkbox" id="oe-messages" ${c.oe_allow_messages!==false?'checked':''}> Let customers message the shop</label>
            <label class="cash-check"><input type="checkbox" id="oe-recommend" ${c.oe_allow_recommend!==false?'checked':''}> Let customers approve recommended services</label>
            <label class="cash-check"><input type="checkbox" id="oe-staff-reply" ${c.oe_allow_staff_reply!==false?'checked':''}> Let staff reply to customers on the public page</label>
            <label class="cash-check"><input type="checkbox" id="oe-scheduled" ${c.oe_show_scheduled?'checked':''}> Show the scheduled date on the tracking page</label>
            <label class="cash-check"><input type="checkbox" id="book-emp-step" ${c.book_show_employee_step!==false?'checked':''}> Online booking: ask the customer to pick an employee</label>
            <div class="pp-hint" style="margin-top:6px">Turning these off hides the buttons on the public pages and blocks the actions. With the employee step off, online bookings arrive unassigned for you to distribute.</div>
          </div>
        </div>

        <div class="pp-foot"><button class="btn btn-primary" onclick="PublicPagesPage.saveOrder()">Save changes</button></div>
      </div>`;
  },

  async saveOrder() {
    try {
      await put('/api/staff/public-pages', {
        oe_show_logo: document.getElementById('oe-logo').checked,
        oe_logo_size: document.getElementById('oe-logo-size').value,
        oe_show_name: document.getElementById('oe-name').checked,
        oe_show_address: document.getElementById('oe-addr').checked,
        oe_show_email: document.getElementById('oe-email').checked,
        oe_show_description: document.getElementById('oe-desc').checked,
        oe_separate_tables: document.getElementById('oe-sep').checked,
        oe_show_zero_price_products: document.getElementById('oe-zp').checked,
        oe_show_zero_price_services: document.getElementById('oe-zs').checked,
        oe_allow_approval: document.getElementById('oe-approval').checked,
        oe_allow_messages: document.getElementById('oe-messages').checked,
        oe_allow_recommend: document.getElementById('oe-recommend').checked,
        oe_allow_staff_reply: document.getElementById('oe-staff-reply').checked,
        oe_show_scheduled: document.getElementById('oe-scheduled').checked,
        book_show_employee_step: document.getElementById('book-emp-step').checked,
      });
      Toast.success('Order / Estimate page saved');
      this._cfg = await get('/api/staff/public-pages');
    } catch (e) { Toast.error(e.message); }
  },

  async changeSubdomain() {
    const cur = (this._cfg && this._cfg.slug) || '';
    const val = window.prompt('New subdomain (3-40 chars, a-z 0-9 -):', cur);
    if (!val || val === cur) return;
    try {
      const r = await put('/api/staff/public-pages/subdomain', { subdomain: val });
      Toast.success('Subdomain changed to ' + r.subdomain);
      this._cfg = await get('/api/staff/public-pages');
      this.setTab('booking');
    } catch (e) { Toast.error(e.message || 'Failed'); }
  },

  copyUrl() {
    const el = document.getElementById('pp-url');
    el.select(); navigator.clipboard?.writeText(el.value);
    Toast.success('Link copied');
  },

  pickTheme(n) {
    this._theme = n;
    document.querySelectorAll('#pp-themes .pp-theme').forEach(el =>
      el.classList.toggle('on', parseInt(el.dataset.theme, 10) === n));
  },

  async save() {
    try {
      await put('/api/staff/public-pages', {
        booking_theme: this._theme || (this._cfg && this._cfg.booking_theme) || 1,
        booking_enabled: document.getElementById('pp-enabled').checked,
        page_color: document.getElementById('pp-color').value,
        page_radius: parseInt(document.getElementById('pp-radius').value, 10),
        page_show_language: document.getElementById('pp-lang').checked,
        page_show_logo: document.getElementById('pp-logo').checked,
        page_background_pattern: document.getElementById('pp-bg').value,
        booking_time_slot: parseInt(document.getElementById('pp-slot').value, 10),
        booking_lead_min: parseInt(document.getElementById('pp-lead').value, 10),
        booking_window_days: parseInt(document.getElementById('pp-window').value, 10),
        booking_allow_multiple: document.getElementById('pp-multi').checked,
        booking_consider_orders: document.getElementById('pp-orders').checked,
      });
      Toast.success('Public pages saved');
      this._cfg = await get('/api/staff/public-pages');
    } catch (e) { Toast.error(e.message); }
  },
};
