/* Public pay page (/pay/{intent_id}). External file so the CSP needs no 'unsafe-inline'. */
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
  const id = location.pathname.split('/').pop();
  const card = document.getElementById('card');
  const money = (a, c) => new Intl.NumberFormat(undefined, { style: 'currency', currency: (c || 'usd').toUpperCase() }).format(a || 0);
  async function load() {
    let d;
    try { d = await (await fetch('/api/public/pay/' + id)).json(); }
    catch (e) { card.innerHTML = '<div class="err">Could not load this payment.</div>'; return; }
    if (d.status === 'succeeded' || d.status === 'paid') { return paid(); }
    if (d.provider !== 'simulated' && (d.status === 'pending' || d.status === 'created')) {
      // Live providers: poll once in case the customer already paid on the hosted page.
      try { const r = await (await fetch('/api/payments/intents/' + id + '/refresh', { method: 'POST' })).json();
            if (r.status === 'succeeded') return paid(); } catch (e) {}
    }
    const items = (d.lines && d.lines.length) ? `<div class="items">${d.lines.map(l =>
        `<div class="row"><span class="d">${esc(l.description)}${l.qty > 1 ? ' × ' + esc(l.qty) : ''}</span><span class="t">${money(l.total, d.currency)}</span></div>`).join('')}</div>` : '';
    const ref = esc(d.invoice_number ? 'Invoice ' + d.invoice_number : (d.order_number ? 'Order ' + d.order_number : ''));
    card.innerHTML = `<div class="logo"></div><h1>Payment request</h1>
      <div class="sub">Tekly</div>
      <div class="amt">${money(d.amount, d.currency)}</div>
      <div class="ord">${ref}</div>
      ${items}
      <button id="pay">Pay ${money(d.amount, d.currency)}</button>
      <div class="err" id="err"></div>`;
    document.getElementById('pay').addEventListener('click', () => pay(d));
  }
  async function pay(d) {
    const btn = document.getElementById('pay'); btn.textContent = 'Processing…'; btn.disabled = true;
    // Live gateway: hand off to its hosted checkout. Simulated: confirm via the public hook.
    if (d.provider !== 'simulated' && d.checkout_url) { location.href = d.checkout_url; return; }
    try {
      const r = await fetch((d.checkout_url || ('/api/payments/intents/' + id + '/simulate-pay')), { method: 'POST' });
      if (!r.ok) throw new Error('failed');
      paid();
    } catch (e) { document.getElementById('err').textContent = 'Payment failed. Please try again.'; btn.textContent = 'Pay'; btn.disabled = false; }
  }
  function paid() { card.innerHTML = '<div class="logo"></div><div class="paid">✓ Payment received</div><div class="sub">Thank you. You can close this page.</div>'; }
  load();
