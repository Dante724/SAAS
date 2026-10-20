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

  plans: [
    {
      id: "starter",
      name: "Starter",
      tagline: "Everything a single shop needs to run the counter and the bench.",
      monthly: 49,
      unit: "per location, per month",
      seats: "Up to 5 team members",
      cta: "Start free trial",
      features: [
        "Work orders, estimates and invoices",
        "Line-by-line approval and signature on the customer's phone",
        "Intake templates with IMEI, VIN, HIN and photo annotation",
        "Checklists and labour timer on every job",
        "Technician mobile app that works offline",
        "Quick POS, cash drawer and payment links",
        "Inventory, barcodes and QR labels",
        "Customer portal, live tracking and online booking",
        "SMS, WhatsApp and email inbox with templates",
        "Branded documents and warranty certificates",
        "Tekly AI: job summaries and reply suggestions",
      ],
    },
    {
      id: "pro",
      name: "Pro",
      tagline: "For busy and multi-location shops that run on parts and people.",
      monthly: 99,
      unit: "per location, per month",
      seats: "Unlimited team members",
      cta: "Start free trial",
      featured: true,
      badge: "Recommended",
      features: [
        "Everything in Starter",
        "Purchase orders that book parts to the job",
        "Multi-location stock, transfers, bins and serial numbers",
        "Payroll, commissions and time clock",
        "Tekly AI: estimate drafting, parts recognition, call insights",
        "Gift cards, coupons and loyalty points",
        "Leads pipeline, feedback and NPS",
        "Automations and a custom report builder",
        "QuickBooks and Xero sync, multi-currency",
        "Role editor, approval limits, audit log and SSO",
        "API keys, webhooks and priority onboarding",
      ],
    },
  ],

  // Shown under the two plans.
  enterprise: {
    name: "Ten or more locations?",
    text: "Franchises, depots and fleets get volume pricing, custom contracts and a named contact.",
    cta: "Talk to us",
  },
};
