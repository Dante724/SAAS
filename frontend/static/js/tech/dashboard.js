/* Technician dashboard view. Renders /api/tech/dashboard into #view. */
window.TechDashboard = (function () {
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  function jobCard(j, overdue) {
    const when = j.scheduled_for ? new Date(j.scheduled_for).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'Unscheduled';
    return `<a class="job ${overdue ? 'ov' : ''}" href="#/order/${encodeURIComponent(j.id)}">
      <div class="row"><span class="num">${esc(j.order_number)}</span><span class="st">${esc(j.status)}</span></div>
      <div class="meta">${esc(j.client)}${j.device ? ' · ' + esc(j.device) : ''}</div>
      <div class="meta">${overdue ? '⚠ Overdue · ' : ''}${when}${j.complaint ? ' · ' + esc(j.complaint.slice(0, 48)) : ''}</div>
    </a>`;
  }
  function section(title, list, overdue) {
    if (!list || !list.length) return '';
    return `<div class="sec-h">${title} (${list.length})</div>` + list.map((j) => jobCard(j, overdue)).join('');
  }

  async function render(el) {
    el.innerHTML = `<div class="empty">Loading your jobs…</div>`;
    let d;
    try { d = await get('/api/tech/dashboard'); }
    catch (e) {
      el.innerHTML = `<div class="empty">Couldn't load jobs. ${navigator.onLine ? esc(e.message) : 'You appear to be offline — cached jobs will show when available.'}</div>`;
      return;
    }
    const s = d.stats;
    const clockTxt = d.clock.clocked_in
      ? `Clocked in${d.clock.category ? ' · ' + d.clock.category : ''}`
      : 'Not clocked in';
    el.innerHTML = `
      <div class="stat-grid">
        <div class="stat"><div class="n">${s.today}</div><div class="l">Today</div></div>
        <div class="stat"><div class="n">${s.upcoming}</div><div class="l">Upcoming</div></div>
        <div class="stat ${s.overdue ? 'alert' : ''}"><div class="n">${s.overdue}</div><div class="l">Overdue</div></div>
        <div class="stat"><div class="n">${s.completed}</div><div class="l">Completed</div></div>
      </div>
      <a class="job" href="#/clock"><div class="row"><span class="num">${esc(clockTxt)}</span>
        <span class="st">${d.clock.clocked_in ? 'Tap to manage' : 'Tap to clock in'}</span></div></a>
      <div id="dash-sections"></div>`;
    const sec = el.querySelector('#dash-sections');
    sec.innerHTML =
      section('Overdue', d.overdue, true) +
      section('Today', d.today, false) +
      section('Upcoming', d.upcoming, false) +
      section('Recently completed', d.completed, false) ||
      `<div class="empty">No jobs assigned to you yet.</div>`;
    if (!d.today.length && !d.upcoming.length && !d.overdue.length && !d.completed.length)
      sec.innerHTML = `<div class="empty">No jobs assigned to you yet.</div>`;
  }
  return { render };
})();
