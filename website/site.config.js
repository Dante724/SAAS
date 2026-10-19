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
      tagline: "For a single bench getting organised.",
      monthly: 49,
      unit: "per location / month",
      seats: "Up to 3 team members",
      cta: "Start free",
      features: [
        "Work orders, estimates & invoices",
        "Customer approvals on their phone",
        "Inventory & barcode labels",
        "Quick POS & cash drawer",
        "Client portal & live job tracking",
        "Tekly AI — job summaries & reply drafts",
      ],
    },
    {
      id: "pro",
      name: "Pro",
      tagline: "For busy shops that run on parts and people.",
      monthly: 99,
      unit: "per location / month",
      seats: "Up to 10 team members",
      cta: "Start free",
      featured: true,
      badge: "Recommended",
      features: [
        "Everything in Starter",
        "Purchase orders that attach parts to jobs",
        "Payroll, commissions & time clock",
        "Tekly AI — estimate drafting & parts recognition",
        "Automations, SMS & email templates",
        "QuickBooks & Xero sync",
        "Reports, job profitability & cash flow",
      ],
    },
    {
      id: "scale",
      name: "Scale",
      tagline: "For multi-location groups and depots.",
      monthly: 199,
      unit: "per location / month",
      seats: "Unlimited team members",
      cta: "Start free",
      features: [
        "Everything in Pro",
        "Multi-location & multi-warehouse stock",
        "Transfers, bins & serialised inventory",
        "Call transcripts with sentiment",
        "Approval rules, audit log & SSO",
        "API keys & webhooks",
        "Priority support & guided onboarding",
      ],
    },
  ],

  // "What it's worth" calculator assumptions (shown to visitors under the result).
  calculator: {
    adminMinutesPerJob: 9,      // estimate + messages + invoice admin saved per job
    approvalUpliftPct: 3,       // extra approved work from line-by-line phone approvals
    callsAvoidedPerJob: 0.6,    // "is it ready?" calls avoided per job
    minutesPerCall: 3,
  },

  enterprise: {
    name: "Enterprise",
    text: "Franchises, OEM depots and fleets with 10+ locations. Custom contracts, data residency and a named success manager.",
    cta: "Talk to sales",
  },
};
