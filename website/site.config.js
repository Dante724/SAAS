/* ─────────────────────────────────────────────────────────────────────────────
   Tekly website — everything you'll want to change without touching layout.
   Edit prices, plan features, links and the demo-form endpoint here.
   ───────────────────────────────────────────────────────────────────────────── */
window.TEKLY_SITE = {
  // Where the Tekly app lives. Sign in / Start free buttons point here.
  appUrl: "http://localhost:8000",
  signInPath: "/login",
  signUpPath: "/signup",

  // Demo requests. If `demoEndpoint` is set, the form POSTs JSON to it.
  // Otherwise it opens the visitor's email app addressed to `demoEmail`.
  demoEndpoint: "",
  demoEmail: "hello@tekly.app",

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
        "Line-by-line approval on the customer's phone",
        "Inventory, barcode and QR labels",
        "Quick POS and cash drawer",
        "Customer portal and live tracking link",
        "Online booking and reminders",
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
        "Purchase orders that attach parts to the job",
        "Payroll, commissions and time clock",
        "Tekly AI: estimate drafting, parts recognition, call insights",
        "Multi-location stock, transfers and serial numbers",
        "QuickBooks and Xero sync",
        "Approval rules, audit log, SSO and API access",
        "Priority support and guided onboarding",
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
