/* ─────────────────────────────────────────────────────────────────────────────
   Tekly website — everything you'll want to change without touching layout.
   Edit prices, plan features, links and the demo-form endpoint here.
   ───────────────────────────────────────────────────────────────────────────── */
window.TEKLY_SITE = {
  // Where the Tekly app lives. Sign in / Start free buttons point here.
  // Local preview of the app: "http://localhost:8000".
  appUrl: "https://app.tekly.app",
  signInPath: "/login",
  signUpPath: "/signup",

  // Demo requests (form and assistant) go to the first of these that's set up:
  //   1. `demoEndpoint` — the form POSTs JSON to it;
  //   2. `assistant.booking.slug` — lands as a web lead in Tekly → Inquiries (needs the
  //      "Tekly Demos" shop with online appointments on, and this site in CORS_ORIGINS);
  //   3. otherwise it opens the visitor's email app addressed to `demoEmail`.
  demoEndpoint: "",
  demoEmail: "hello@tekly.app",

  // Analytics: set a Google Tag Manager ID (e.g. "GTM-ABC123") to load GTM. Left empty, nothing
  // third-party loads; CTA clicks and demo requests still go to window.dataLayer.
  analytics: { gtmId: "" },

  // On-page assistant (no AI service; runs in the browser). To book real demo times, create a
  // "Tekly Demos" shop in Tekly, turn on online appointments, and put its booking slug here.
  // Add this website's domain to the backend's CORS_ORIGINS. Empty slug = it collects a
  // preferred day/time and sends a demo request instead.
  assistant: {
    enabled: true,
    name: "Tekly Assistant",
    nudgeAfterSec: 25,
    booking: { slug: "", apiBase: "", timezoneLabel: "" },
  },

  currency: "$",
  yearlyMonths: 10,      // yearly price = 10 x monthly ("2 months free"), as in billing.py
  trialDays: 14,

  // Plans — mirror backend/services/billing.py (PLANS, MONTHLY_PRICE, PLAN_LIMITS, FEATURES).
  // Keep the two in step: prices are USD; yearly = yearlyMonths x monthly.
  plans: [
    {
      id: "solo",
      name: "Solo",
      tagline: "One technician and a helper, getting organised.",
      monthly: 29,
      unit: "per month",
      seats: "2 team members · 1 location · 150 new jobs a month",
      cta: "Start free trial",
      features: [
        "Work orders, estimates and invoices",
        "Line-by-line approval and signature on the customer's phone",
        "Quick POS, payment links and customer portal",
        "Online booking and live tracking link",
        "Tekly Go phone app for techs",
        "One-tap texts, WhatsApp and email",
        "Intake templates, photos, checklists and labour timer",
        "Tekly AI job summaries and reply suggestions",
        "Tekly Sense suggestions and Morning Brief",
      ],
    },
    {
      id: "shop",
      name: "Shop",
      tagline: "A full shop with a team, stock and regular customers.",
      monthly: 69,
      extraLocation: 49,
      extraSeat: 6,
      unit: "for the first location, per month",
      seats: "4 team members shared across your locations · unlimited jobs",
      cta: "Start free trial",
      featured: true,
      badge: "Best for most shops",
      features: [
        "Everything in Solo",
        "Inventory, stock and suppliers",
        "Barcodes, labels and scanning",
        "Time clock and timesheets",
        "Reports, analytics and custom reports",
        "Automatic customer notifications and automations",
      ],
    },
    {
      id: "pro",
      name: "Pro",
      tagline: "Bigger teams, chains and shops that move stock between sites.",
      monthly: 129,
      extraLocation: 79,
      unit: "for the first location, per month",
      extraSeat: 6,
      seats: "8 team members shared across your locations · unlimited jobs",
      cta: "Start free trial",
      features: [
        "Everything in Shop",
        "Purchase orders",
        "Payroll and commissions",
        "Stock transfers between locations",
        "AI estimate drafting and parts recognition",
        "QuickBooks and Xero sync",
        "Single sign-on and API keys",
      ],
    },
  ],

  // Shown under the plans.
  enterprise: {
    name: "Ten or more locations?",
    text: "Shop adds locations at $49 each and Pro at $79 each, as many as you need. Franchises, depots and fleets can ask us about custom terms.",
    cta: "Talk to us",
  },
};
