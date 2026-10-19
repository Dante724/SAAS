/* Marketing helpers — no third-party tracking unless configured in site.config.js.
   1. Keeps UTM / ad-click parameters from the landing URL for the visit and appends them to
      every sign-up and sign-in link, so the app can attribute trials to campaigns.
   2. Pushes CTA clicks and demo requests to window.dataLayer (GTM/GA4-compatible).
   3. Loads Google Tag Manager only if TEKLY_SITE.analytics.gtmId is set. */
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
  document.addEventListener("tekly:demo_request", e => push("generate_lead", { form: "demo", industry: e.detail?.industry || "", locations: e.detail?.locations || "", ...attr }));

  const id = C.analytics && C.analytics.gtmId;
  if (id && /^GTM-[A-Z0-9]+$/.test(id)) {
    push("gtm.js", { "gtm.start": Date.now() });
    const s = document.createElement("script"); s.async = true; s.src = "https://www.googletagmanager.com/gtm.js?id=" + id;
    document.head.appendChild(s);
  }
})();
