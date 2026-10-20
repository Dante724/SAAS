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

  // Demo requests. If `demoEndpoint` is set, the form POSTs JSON to it.
  // Otherwise it opens the visitor's email app addressed to `demoEmail`.
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
  annualDiscount: 0.2,   // 20% off when billed yearly
  trialDays: 14,

  // Plans, derived from what the app does today. Change prices, limits and features here;
  // the pricing section, structured data, FAQ and assistant all read from this list.
  plans: [
    {
      id: "starter",
      name: "Starter",
      tagline: "One shop getting the counter and the bench organised.",
      monthly: 49,
      unit: "per location, per month",
      seats: "Up to 3 team members",
      cta: "Start free trial",
      features: [
        "Work orders, estimates and invoices",
        "Line-by-line approval and signature on the customer's phone",
        "Intake templates (IMEI, VIN, HIN) with photo annotation",
        "Checklists and labour timer on every job",
        "Technician mobile app that works offline",
        "Quick POS, cash drawer and payment links",
        "Inventory, barcodes and QR labels",
        "Customer portal, live tracking and online booking",
        "SMS, WhatsApp and email inbox",
        "Branded documents and warranty certificates",
        "Tekly AI: job summaries and reply suggestions",
        "Tekly Sense: Morning Brief",
      ],
    },
    {
      id: "pro",
      name: "Pro",
      tagline: "Busy shops that run on parts, people and repeat customers.",
      monthly: 99,
      unit: "per location, per month",
      seats: "Up to 15 team members",
      cta: "Start free trial",
      featured: true,
      badge: "Most shops",
      features: [
        "Everything in Starter",
        "Purchase orders that book parts to the job",
        "Payroll, commissions and time clock",
        "Tekly AI: estimate drafting and parts recognition",
        "Tekly Sense: suggested lines, usually added together, service due, margin check",
        "Gift cards, coupons and loyalty points",
        "Leads pipeline, feedback and NPS",
        "Automations and custom report builder",
        "QuickBooks and Xero sync",
      ],
    },
    {
      id: "business",
      name: "Business",
      tagline: "Multi-location shops, franchises and fleets that need control.",
      monthly: 199,
      unit: "per location, per month",
      seats: "Unlimited team members",
      cta: "Start free trial",
      features: [
        "Everything in Pro",
        "Multi-location stock, transfers, bins and serial numbers",
        "Tekly Sense: Watchdog and team scorecard",
        "Tekly AI: call transcripts and sentiment",
        "Role editor and approval limits for refunds and discounts",
        "Single sign-on (SSO)",
        "Multi-currency with rate history",
        "API keys and webhooks",
        "Priority support and guided onboarding",
      ],
    },
  ],

  // Shown under the plans.
  enterprise: {
    name: "Ten or more locations?",
    text: "Volume pricing on Business, custom contracts and a named contact for franchises, depots and fleets.",
    cta: "Talk to us",
  },
};
