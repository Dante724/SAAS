/* Build 144db — custom glass datepicker that replaces Chrome's native calendar
   on every date / datetime-local field, site-wide, with zero per-page changes.

   How: delegated listeners on `.field-input[type=date|datetime-local]`. The field
   stays the source of truth (its value keeps the native `YYYY-MM-DD` /
   `YYYY-MM-DDTHH:MM` format), so existing read/write code is untouched. We set the
   field readonly on first touch to suppress the native popup, then open our own
   popover, and on pick we write the value back and dispatch input+change so every
   existing handler still fires. Honours min / max attributes. */
(function () {
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];
  const DOW = ['Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa', 'Su'];
  const pad = (n) => String(n).padStart(2, '0');
  const isMatch = (el) => el && el.matches &&
    el.matches('input.field-input[type="date"], input.field-input[type="datetime-local"]');

  let pop = null, curInput = null, viewY = 0, viewM = 0, selDate = null, timeH = 9, timeMin = 0;

  function parse(val, hasTime) {
    // returns {d: Date|null}
    if (!val) return null;
    const m = val.match(/^(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}))?/);
    if (!m) return null;
    return { y: +m[1], mo: +m[2] - 1, d: +m[3], h: m[4] != null ? +m[4] : (hasTime ? 9 : 0), mi: m[5] != null ? +m[5] : 0 };
  }
  function fmt(y, mo, d, hasTime) {
    const base = `${y}-${pad(mo + 1)}-${pad(d)}`;
    return hasTime ? `${base}T${pad(timeH)}:${pad(timeMin)}` : base;
  }
  function attrDate(el, name) { const p = parse(el.getAttribute(name), false); return p ? new Date(p.y, p.mo, p.d) : null; }

  function close() {
    if (pop) { pop.remove(); pop = null; }
    curInput = null;
    document.removeEventListener('mousedown', onDocDown, true);
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('resize', close);
    window.removeEventListener('scroll', close, true);
  }
  function onDocDown(e) { if (pop && !pop.contains(e.target) && e.target !== curInput) close(); }
  function onKey(e) { if (e.key === 'Escape') close(); }

  function open(input) {
    if (pop && curInput === input) return;
    close();
    curInput = input;
    input.dataset.rdp = '1'; input.readOnly = true; input.autocomplete = 'off';
    const hasTime = input.type === 'datetime-local';
    const p = parse(input.value, hasTime);
    const now = new Date();
    viewY = p ? p.y : now.getFullYear();
    viewM = p ? p.mo : now.getMonth();
    selDate = p ? new Date(p.y, p.mo, p.d) : null;
    timeH = p ? p.h : 9; timeMin = p ? p.mi : 0;

    pop = document.createElement('div');
    pop.className = 'rdp';
    pop.setAttribute('role', 'dialog');
    document.body.appendChild(pop);
    render(hasTime);
    position(input);
    document.addEventListener('mousedown', onDocDown, true);
    document.addEventListener('keydown', onKey, true);
    window.addEventListener('resize', close);
    window.addEventListener('scroll', close, true);
  }

  function position(input) {
    const r = input.getBoundingClientRect();
    const w = pop.offsetWidth || 300, h = pop.offsetHeight || 320;
    let top = r.bottom + 6, left = r.left;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6); // flip up
    if (left + w > window.innerWidth - 8) left = Math.max(8, window.innerWidth - w - 8);
    pop.style.top = `${top}px`;
    pop.style.left = `${left}px`;
  }

  function render(hasTime) {
    const first = new Date(viewY, viewM, 1);
    const startDow = (first.getDay() + 6) % 7;          // Monday-first
    const days = new Date(viewY, viewM + 1, 0).getDate();
    const min = curInput ? attrDate(curInput, 'min') : null;
    const max = curInput ? attrDate(curInput, 'max') : null;
    const today = new Date(); today.setHours(0, 0, 0, 0);

    let cells = '';
    for (let i = 0; i < startDow; i++) cells += `<div class="rdp-pad"></div>`;
    for (let d = 1; d <= days; d++) {
      const cellDate = new Date(viewY, viewM, d);
      const dis = (min && cellDate < min) || (max && cellDate > max);
      const cls = ['rdp-day'];
      if (selDate && cellDate.getTime() === selDate.getTime()) cls.push('is-sel');
      else if (cellDate.getTime() === today.getTime()) cls.push('is-today');
      if (dis) cls.push('is-dis');
      cells += `<button type="button" class="${cls.join(' ')}" ${dis ? 'disabled' : ''} data-d="${d}">${d}</button>`;
    }

    const timeRow = hasTime ? `
      <div class="rdp-time">
        <span>Time</span>
        <select class="rdp-h">${Array.from({ length: 24 }, (_, i) => `<option value="${i}" ${i === timeH ? 'selected' : ''}>${pad(i)}</option>`).join('')}</select>
        <span>:</span>
        <select class="rdp-m">${Array.from({ length: 12 }, (_, i) => i * 5).map(mm => `<option value="${mm}" ${mm === timeMin ? 'selected' : ''}>${pad(mm)}</option>`).join('')}</select>
      </div>` : '';

    pop.innerHTML = `
      <div class="rdp-head">
        <button type="button" class="rdp-nav" data-nav="-1">&#8249;</button>
        <div class="rdp-title">${MONTHS[viewM]} ${viewY}</div>
        <button type="button" class="rdp-nav" data-nav="1">&#8250;</button>
      </div>
      <div class="rdp-dows">${DOW.map(x => `<div>${x}</div>`).join('')}</div>
      <div class="rdp-grid">${cells}</div>
      ${timeRow}
      <div class="rdp-foot">
        <button type="button" class="rdp-act rdp-today">Today</button>
        <button type="button" class="rdp-act rdp-clear">Clear</button>
      </div>`;

    pop.querySelectorAll('.rdp-nav').forEach(b => b.onclick = () => {
      viewM += +b.dataset.nav;
      if (viewM < 0) { viewM = 11; viewY--; } else if (viewM > 11) { viewM = 0; viewY++; }
      render(hasTime); position(curInput);
    });
    pop.querySelectorAll('.rdp-day:not(.is-dis)').forEach(b => b.onclick = () => pick(+b.dataset.d, hasTime));
    const hSel = pop.querySelector('.rdp-h'), mSel = pop.querySelector('.rdp-m');
    if (hSel) hSel.onchange = () => { timeH = +hSel.value; };
    if (mSel) mSel.onchange = () => { timeMin = +mSel.value; };
    pop.querySelector('.rdp-today').onclick = () => {
      const t = new Date(); viewY = t.getFullYear(); viewM = t.getMonth();
      pick(t.getDate(), hasTime);
    };
    pop.querySelector('.rdp-clear').onclick = () => { commit(''); close(); };
  }

  function pick(d, hasTime) {
    commit(fmt(viewY, viewM, d, hasTime));
    close();
  }
  function commit(value) {
    if (!curInput) return;
    curInput.value = value;
    curInput.dispatchEvent(new Event('input', { bubbles: true }));
    curInput.dispatchEvent(new Event('change', { bubbles: true }));
  }

  // Delegated open. Suppress the native picker by marking the field readonly on
  // first touch (keeps value + submission, blocks the OS calendar and typing).
  function onDown(e) {
    const el = e.target;
    if (!isMatch(el)) return;
    if (!el.dataset.rdp) { el.dataset.rdp = '1'; el.readOnly = true; el.autocomplete = 'off'; }
    e.preventDefault();
    open(el);
  }
  document.addEventListener('mousedown', onDown, true);
  document.addEventListener('focusin', (e) => { if (isMatch(e.target) && !pop) { e.target.dataset.rdp = '1'; e.target.readOnly = true; } }, true);
  // Keyboard access: open on Enter / Space / ArrowDown when a date field has focus.
  document.addEventListener('keydown', (e) => {
    if (!isMatch(e.target)) return;
    if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') {
      e.preventDefault();
      if (pop && curInput === e.target) close(); else open(e.target);
    }
  }, true);

  window.DatePicker = { open, close };
})();
