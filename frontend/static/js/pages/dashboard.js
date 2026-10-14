/* ════════════════════════════════════════════════════
   Dashboard — "My company" (Orderry-style, premium)
════════════════════════════════════════════════════ */

const GROUP_META = {
  new:         { label: 'New',         color: 'var(--blue)' },
  in_progress: { label: 'In progress', color: 'var(--green)' },
  pending:     { label: 'Pending',     color: 'var(--amber)' },
  ready:       { label: 'Ready',       color: 'var(--teal)' },
  closed:      { label: 'Done',        color: 'var(--tx-3)' },
  dropped:     { label: 'Dropped',     color: 'var(--red)' },
};

Router.register('dashboard', async () => {
  setTopbarActions(`
    <div class="topbar-pill"><span>${icon('clock',13)}</span><span class="val" id="live-time">—</span></div>
    <button class="btn btn-glass btn-sm" onclick="Router.go('dashboard')">${icon('refresh',13)} Refresh</button>
  `);
  setContent(`
    <div class="dash-head">
      <div><h2 class="dash-title">My company</h2><div class="dash-sub">Key performance metrics. Data may be delayed a few minutes.</div></div>
    </div>
    <div class="dash-today" id="dash-today">${Array(4).fill('<div class="card skel-tile"></div>').join('')}</div>
    <div class="dash-kpis" id="dash-kpis">${Array(5).fill('<div class="card skel-tile"></div>').join('')}</div>
    <div class="dash-panels">
      <div class="card dash-panel glass-liquid"><div class="dp-title">Work orders in progress</div><div id="dash-donut" class="dp-body"></div></div>
      <div class="card dash-panel glass-liquid"><div class="dp-title">Orders by assignee</div><div id="dash-assignee" class="dp-body"></div></div>
    </div>
    <div class="dash-monthly" id="dash-monthly">${Array(4).fill('<div class="card skel-tile"></div>').join('')}</div>
    <div class="card dash-panel" style="margin-top:16px">
      <div class="dp-head"><div class="dp-title">Recent work orders</div><button class="btn btn-glass btn-sm" onclick="Router.go('orders')">View all →</button></div>
      <div class="tbl-wrap" style="padding:0"><table class="tbl"><thead>
        <tr><th>Order #</th><th>Status</th><th>Priority</th><th>Total</th><th>Created</th></tr>
      </thead><tbody id="dash-recent">${skeletonRows(6)}</tbody></table></div>
    </div>
  `);

  function tick() { const el = document.getElementById('live-time'); if (el) el.textContent = new Date().toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit'}); }
  tick(); setInterval(tick, 10000);

  try {
    const _lp = (typeof App !== 'undefined' && App.locParam) ? App.locParam('?') : '';
    const [cd, dash] = await Promise.all([
      get(`/api/reports/company-dashboard${_lp}`),
      get('/api/reports/dashboard'),
    ]);

    document.getElementById('dash-today').innerHTML = [
      { label:'Work orders today', val:cd.today.work_orders, cls:'clay-sky' },
      { label:'Revenue today', val:fmt.currency(cd.today.revenue), cls:'clay-mint' },
      { label:'Refunds today', val:fmt.currency(cd.today.refunds), cls:'clay-coral' },
      { label:'Total money in accounts', val:fmt.currency(cd.kpis.total_money), cls:'clay-indigo',
        sub:`Cash ${fmt.currency(cd.kpis.cash)} · Cashless ${fmt.currency(cd.kpis.cashless)}` },
    ].map(t => `<div class="dash-tile clay-tile ${t.cls}">
        <div class="dt-label">${t.label}</div>
        <div class="dt-val">${t.val}</div>
        ${t.sub?`<div class="dt-sub">${t.sub}</div>`:''}
      </div>`).join('');

    const total = cd.kpis.new + cd.kpis.urgent + cd.kpis.overdue || 1;
    document.getElementById('dash-kpis').innerHTML = [
      { label:'New', val:cd.kpis.new, cls:'clay-sky', pct:Math.round(cd.kpis.new/total*100)+'%' },
      { label:'Urgent', val:cd.kpis.urgent, cls:'clay-coral', pct:Math.round(cd.kpis.urgent/total*100)+'%' },
      { label:'Overdue', val:cd.kpis.overdue, cls:'clay-butter' },
      { label:'Receivables', val:fmt.currency(cd.kpis.receivables), cls:'clay-violet' },
      { label:'Conversion (month)', val:cd.monthly.conversion+'%', cls:'clay-mint' },
    ].map(k => `<div class="dash-tile clay-tile sm ${k.cls}">
        <div class="dt-label">${k.label}</div>
        <div class="dt-val">${k.val}</div>
        ${k.pct?`<div class="dt-pill">${k.pct}</div>`:''}
      </div>`).join('');

    document.getElementById('dash-donut').innerHTML = donutChart(cd.status_mix);

    const maxA = Math.max(...cd.by_assignee.map(a=>a.total), 1);
    document.getElementById('dash-assignee').innerHTML = cd.by_assignee.length
      ? `<div class="assignee-chart">${cd.by_assignee.map(a => `
          <div class="ac-row">
            <div class="ac-name" title="${a.name}">${a.name}</div>
            <div class="ac-track">
              <div class="ac-seg" style="width:${a.new/maxA*100}%;background:var(--blue)"></div>
              <div class="ac-seg" style="width:${a.in_progress/maxA*100}%;background:var(--green)"></div>
            </div>
            <div class="ac-val">${a.total}</div>
          </div>`).join('')}
          <div class="ac-legend"><span><i style="background:var(--blue)"></i>New</span><span><i style="background:var(--green)"></i>In progress</span></div>
        </div>`
      : '<div class="dp-empty">No open orders</div>';

    document.getElementById('dash-monthly').innerHTML = [
      { label:'Work orders created', val:cd.monthly.work_orders_created, cls:'t-blue' },
      { label:'Work orders won', val:cd.monthly.work_orders_won, cls:'t-green' },
      { label:'Revenue this month', val:fmt.currency(cd.monthly.revenue), cls:'t-green' },
      { label:'Average work order value', val:fmt.currency(cd.monthly.avg_work_order), cls:'t-gray' },
    ].map(t => `<div class="dash-tile sm ${t.cls}"><div class="dt-label">${t.label}</div><div class="dt-val">${t.val}</div></div>`).join('');

    // build 144ad: premium number count-up on the metric tiles (reduced-motion aware)
    try { if (window.countUpAll) ['dash-today', 'dash-kpis', 'dash-monthly'].forEach(id => countUpAll(document.getElementById(id))); } catch (e) {}

    const tb = document.getElementById('dash-recent');
    tb.innerHTML = dash.recent_orders.length
      ? dash.recent_orders.map(o => `<tr onclick="OrdersPage.viewOrder('${o.id}')" style="cursor:pointer">
          <td><span class="tbl-num tbl-strong">${o.order_number}</span></td>
          <td>${badge(o.status)}</td><td>${priBadge(o.priority)}</td>
          <td><span class="tbl-strong">${fmt.currency(o.total)}</span></td>
          <td style="color:var(--tx-3)">${fmt.date(o.created_at)}</td></tr>`).join('')
      : emptyRow(5,'wrench','No orders yet','Create your first work order');

  } catch (e) { Toast.error('Dashboard error: ' + e.message); }
});

function donutChart(mix) {
  const data = (mix || []).filter(m => m.count > 0);
  const sum = data.reduce((s,m)=>s+m.count,0);
  if (!sum) return '<div class="dp-empty">No work orders in progress</div>';
  const R = 70, r = 44, cx = 90, cy = 90;
  let a0 = -Math.PI/2;
  const segs = data.map(m => {
    const frac = m.count/sum, a1 = a0 + frac*2*Math.PI;
    const meta = GROUP_META[m.group] || { label:m.group, color:'var(--tx-3)' };
    const big = frac > 0.5 ? 1 : 0;
    const x0=cx+R*Math.cos(a0), y0=cy+R*Math.sin(a0), x1=cx+R*Math.cos(a1), y1=cy+R*Math.sin(a1);
    const xi0=cx+r*Math.cos(a1), yi0=cy+r*Math.sin(a1), xi1=cx+r*Math.cos(a0), yi1=cy+r*Math.sin(a0);
    const d = `M${x0},${y0} A${R},${R} 0 ${big} 1 ${x1},${y1} L${xi0},${yi0} A${r},${r} 0 ${big} 0 ${xi1},${yi1} Z`;
    a0 = a1;
    return { d, meta, count:m.count };
  });
  return `<div class="donut-wrap">
    <svg viewBox="0 0 180 180" width="170" height="170">
      ${segs.map(s=>`<path d="${s.d}" fill="${s.meta.color}" stroke="var(--card,#fff)" stroke-width="2"/>`).join('')}
      <text x="90" y="86" text-anchor="middle" class="donut-total">${sum}</text>
      <text x="90" y="104" text-anchor="middle" class="donut-cap">in progress</text>
    </svg>
    <div class="donut-legend">${segs.map(s=>`<div class="dl-row"><i style="background:${s.meta.color}"></i>${s.meta.label}<span>${s.count}</span></div>`).join('')}</div>
  </div>`;
}
