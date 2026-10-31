/* Public pay page (/pay/{intent_id}). External file so the CSP needs no 'unsafe-inline'.
   Branded with the shop asking for the money, not with us: a customer who has never heard of
   Tekly must recognise who is asking them to pay. A link that can't be paid says so plainly,
   and never shows a live Pay button. */
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const id = location.pathname.split('/').pop();
  const card = document.getElementById('card');
  const money = (a, c) => new Intl.NumberFormat(undefined, { style: 'currency', currency: (c || 'usd').toUpperCase() }).format(a || 0);

  function shopHeader(d) {
    if (!d || !d.shop_name) return '';
    const logo = (typeof d.logo_data === 'string' && /^data:image\//.test(d.logo_data))
      ? `<img src="${esc(d.logo_data)}" alt="">`
      : `<div class="mono" aria-hidden="true">${esc(d.shop_name.trim().charAt(0).toUpperCase())}</div>`;
    const addr = d.shop_address ? `<div class="a">${esc(d.shop_address)}</div>` : '';
    return `<div class="shop">${logo}<div><div class="n">${esc(d.shop_name)}</div>${addr}</div></div>`;
  }
  function contact(d) {
    const name = d && d.shop_name ? esc(d.shop_name) : 'the shop';
    const tel = d && d.shop_phone ? ` at <a href="tel:${esc(d.shop_phone.replace(/[^\d+]/g, ''))}">${esc(d.shop_phone)}</a>` : '';
    return `Please contact ${name}${tel} for a new link.`;
  }
  function invalid(d) {
    if (d && d.shop_name) document.title = 'Payment request · ' + d.shop_name;
    card.innerHTML = `${shopHeader(d)}<div class="body"><div class="state">
      <div class="badge bad" aria-hidden="true">!</div>
      <h1>This link isn't valid</h1>
      <p>It may have expired or been cancelled. ${contact(d)}</p></div></div>`;
  }
  function paid(d) {
    card.innerHTML = `${shopHeader(d)}<div class="body"><div class="state">
      <div class="badge ok" aria-hidden="true">✓</div>
      <h1>Payment received</h1>
      <p>Thank you${d && d.shop_name ? ', ' + esc(d.shop_name) + ' has been notified' : ''}. You can close this page.</p></div></div>`;
  }

  async function load() {
    let d;
    try {
      const r = await fetch('/api/public/pay/' + encodeURIComponent(id));
      if (!r.ok) return invalid(null);
      d = await r.json();
    } catch (e) { card.innerHTML = '<div class="body"><div class="state"><h1>Couldn\'t load this payment</h1><p>Check your connection and refresh the page.</p></div></div>'; return; }
    if (d.shop_name) document.title = 'Payment request · ' + d.shop_name;
    if (d.status === 'succeeded' || d.status === 'paid') return paid(d);
    if (!d.payable || !(d.amount > 0)) return invalid(d);
    if (d.provider !== 'simulated' && (d.status === 'pending' || d.status === 'created')) {
      // Live providers: poll once in case the customer already paid on the hosted page.
      try { const r = await (await fetch('/api/payments/intents/' + id + '/refresh', { method: 'POST' })).json();
            if (r.status === 'succeeded') return paid(d); } catch (e) {}
    }
    const items = (d.lines && d.lines.length) ? `<div class="items">${d.lines.map(l =>
        `<div class="row"><span class="d">${esc(l.description)}${l.qty > 1 ? ' × ' + esc(l.qty) : ''}</span><span class="t">${money(l.total, d.currency)}</span></div>`).join('')}
        <div class="row total"><span class="d">Total</span><span class="t">${money(d.amount, d.currency)}</span></div></div>` : '';
    const ref = esc(d.invoice_number ? 'Invoice ' + d.invoice_number : (d.order_number ? 'Order ' + d.order_number : ''));
    card.innerHTML = `${shopHeader(d)}<div class="body">
      <div class="lbl">Amount due</div>
      <div class="amt">${money(d.amount, d.currency)}</div>
      <div class="ref">${ref}</div>
      ${items}
      <button id="pay" type="button">Pay ${money(d.amount, d.currency)}</button>
      <div class="err" id="err" role="alert"></div></div>`;
    document.getElementById('pay').addEventListener('click', () => pay(d));
  }
  async function pay(d) {
    const btn = document.getElementById('pay'); const label = btn.textContent;
    btn.textContent = 'Processing…'; btn.disabled = true;
    // Live gateway: hand off to its hosted checkout. Simulated: confirm via the public hook.
    if (d.provider !== 'simulated' && d.checkout_url) { location.href = d.checkout_url; return; }
    try {
      const r = await fetch((d.checkout_url || ('/api/payments/intents/' + id + '/simulate-pay')), { method: 'POST' });
      if (!r.ok) throw new Error('failed');
      paid(d);
    } catch (e) { document.getElementById('err').textContent = 'Payment failed. Please try again.'; btn.textContent = label; btn.disabled = false; }
  }
  load();
