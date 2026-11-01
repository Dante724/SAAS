/* Marketing helpers — no third-party tracking unless configured in site.config.js.
   1. Keeps UTM / ad-click parameters from the landing URL for the visit and appends them to
      every sign-up and sign-in link, so the app can attribute trials to campaigns.
   2. Pushes CTA clicks and demo requests to window.dataLayer (GTM/GA4-compatible).
   3. Loads Google Tag Manager only if TEKLY_SITE.analytics.gtmId is set.
   4. window.TeklyLead.send(): delivers a demo request to the first destination that's set up —
      demoEndpoint, else Tekly's own Inquiries board (the "Tekly Demos" shop's booking slug),
      else the visitor's email app. Returns "sent" or "mailto". */
(() => {
  const C = window.TEKLY_SITE || {};
  const KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "msclkid", "ref"];
  window.dataLayer = window.dataLayer || [];
  const push = (event, data) => window.dataLayer.push({ event, ...data });

  let attr = {};
  try { attr = JSON.parse(sessionStorage.getItem("tekly_attr") || "{}"); } catch {}
  const q = new URLSearchParams(location.search);
  KEYS.forEach(k => { if (q.get(k)) attr[k] = q.get(k).slice(0, 120); });
  if (!attr.landing) attr.landing = location.pathname;
  if (!attr.referrer && document.referrer && !document.referrer.startsWith(location.origin)) attr.referrer = document.referrer.slice(0, 200);
  try { sessionStorage.setItem("tekly_attr", JSON.stringify(attr)); } catch {}
  window.TEKLY_ATTRIBUTION = attr;

  const decorate = () => document.querySelectorAll('a[data-app], .plan a.btn').forEach(a => {
    try {
      const u = new URL(a.href, location.href);
      if (u.origin === location.origin && u.pathname === location.pathname) return;   // not an app link yet
      KEYS.forEach(k => { if (attr[k] && !u.searchParams.has(k)) u.searchParams.set(k, attr[k]); });
      a.href = u.toString();
    } catch {}
  });
  decorate();
  document.addEventListener("tekly:links-ready", decorate);
  new MutationObserver(decorate).observe(document.getElementById("plans") || document.createElement("i"), { childList: true });

  document.addEventListener("click", e => {
    const a = e.target.closest("a.btn, a.link, a.nav-signin, button.btn, .chip");
    if (!a) return;
    push("cta_click", { cta: a.dataset.cta || a.textContent.trim().slice(0, 40), cta_href: a.getAttribute("href") || "", page: location.pathname });
  });

  const lines = d => [d.shop && "Shop: " + d.shop, d.industry && "Trade: " + d.industry,
    d.locations && "Locations: " + d.locations, d.current && "Using today: " + d.current,
    d.preferred && "Preferred time: " + d.preferred, d.message && "Note: " + d.message,
    attr.utm_source && "Source: " + attr.utm_source + (attr.utm_campaign ? "/" + attr.utm_campaign : ""),
    attr.landing && "Landing page: " + attr.landing].filter(Boolean);
  const post = async (url, body) => {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      signal: AbortSignal.timeout ? AbortSignal.timeout(10000) : undefined });
    if (!r.ok) throw Object.assign(new Error("HTTP " + r.status), { status: r.status });
  };
  window.TeklyLead = {
    /* Tries each configured route in order; a failure falls through to the next one, ending
       with the visitor's email app, so a request is never lost to a server error. */
    async send(d, source = "website") {
      const inbox = (C.assistant && C.assistant.booking) || {};
      const routes = [];
      if (C.demoEndpoint) routes.push(() => post(C.demoEndpoint, { ...d, source, ...attr }));
      if (inbox.slug) {   // lands as a web lead in Tekly → Inquiries, with the reply-time clock running
        const base = (inbox.apiBase || (C.appUrl || "").replace(/\/$/, "") + "/api/public").replace(/\/$/, "");
        routes.push(() => post(`${base}/appointment/${encodeURIComponent(inbox.slug)}/request`, {
          name: d.name, email: d.email, phone: d.phone || null,
          service_name: "Demo request (" + source + ")", issue: lines(d).join(" · ").slice(0, 1900) }));
      }
      for (const go of routes) {
        try { await go(); push("generate_lead", { form: source, delivery: "sent", industry: d.industry || "", locations: d.locations || "", ...attr }); return "sent"; }
        catch (err) { push("lead_route_failed", { form: source, error: String(err && err.message || err).slice(0, 80) }); }
      }
      const body = [`Name: ${d.name}`, `Email: ${d.email}`, d.phone && `Phone: ${d.phone}`, ...lines(d)].filter(Boolean).join("\n");
      push("generate_lead", { form: source, delivery: "mailto", industry: d.industry || "", locations: d.locations || "", ...attr });
      location.href = `mailto:${C.demoEmail}?subject=${encodeURIComponent("Tekly demo request — " + (d.shop || d.name))}&body=${encodeURIComponent(body)}`;
      return "mailto";
    },
  };

  const id = C.analytics && C.analytics.gtmId;
  if (id && /^GTM-[A-Z0-9]+$/.test(id)) {
    push("gtm.js", { "gtm.start": Date.now() });
    const s = document.createElement("script"); s.async = true; s.src = "https://www.googletagmanager.com/gtm.js?id=" + id;
    document.head.appendChild(s);
  }
})();
