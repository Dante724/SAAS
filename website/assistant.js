/* Tekly Assistant — runs entirely in the browser. No AI service is called.
   It answers from a built-in knowledge base (fuzzy, typo-tolerant matching), remembers the
   visitor's trade, recommends a plan, and books a demo:
     • with TEKLY_SITE.assistant.booking.slug set → real open times from Tekly's own booking API
       (GET {apiBase}/appointment/{slug}/days|slots, POST {apiBase}/appointment/{slug})
     • without it → collects a preferred day and time and sends a demo request
       (demoEndpoint, or the visitor's email app). */
(() => {
  const C = window.TEKLY_SITE || {};
  const A = Object.assign({ enabled: true, name: "Tekly Assistant", nudgeAfterSec: 25 }, C.assistant || {});
  if (!A.enabled) return;
  const B = Object.assign({ slug: "", apiBase: "", timezoneLabel: "" }, A.booking || {});
  const apiBase = (B.apiBase || ((C.appUrl || "").replace(/\/$/, "") + "/api/public")).replace(/\/$/, "");
  const LIVE = !!B.slug;
  const cur = C.currency || "$";
  const plans = C.plans || [];
  const solo = plans.find(p => p.id === "solo") || { monthly: 29, name: "Solo" };
  const shop = plans.find(p => p.id === "shop") || { monthly: 69, extraLocation: 49, extraSeat: 4, name: "Shop" };
  const pro = plans.find(p => p.id === "pro") || { monthly: 129, extraLocation: 79, name: "Pro" };
  const ymo = C.yearlyMonths ?? 10;
  const trial = C.trialDays ?? 14;
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const push = (event, data = {}) => (window.dataLayer = window.dataLayer || []).push({ event, ...data });
  const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ── Memory (survives page changes within the visit) ── */
  const KEY = "tekly_assistant";
  let mem = { trade: null, locations: null, msgs: [], open: false, nudged: false };
  try { Object.assign(mem, JSON.parse(sessionStorage.getItem(KEY) || "{}")); } catch {}
  if (!mem.trade) { const pg = { "/phone-repair-software/": "electronics", "/auto-repair-shop-software/": "vehicles", "/small-engine-repair-software/": "tools", "/marine-repair-software/": "marine" }[location.pathname]; if (pg) mem.trade = pg; }
  const save = () => { try { sessionStorage.setItem(KEY, JSON.stringify({ ...mem, msgs: mem.msgs.slice(-40) })); } catch {} };

  /* ── Trades ── */
  const TRADES = {
    electronics: { name: "Electronics", words: ["phone", "iphone", "android", "samsung", "tablet", "ipad", "laptop", "computer", "pc", "mac", "macbook", "console", "electronics", "screen", "imei"], page: "/phone-repair-software/",
      blurb: "The Electronics template covers phones, tablets, laptops and desktops. At intake it asks for the IMEI on phones and tablets and the serial number on computers, and stores passcodes encrypted." },
    vehicles: { name: "Vehicles", words: ["car", "cars", "auto", "automotive", "truck", "trucks", "fleet", "rv", "rvs", "motorhome", "camper", "bike", "bicycle", "ebike", "motorcycle", "motorbike", "vehicle", "garage", "vin", "mechanic"], page: "/auto-repair-shop-software/",
      blurb: "The Vehicles template covers cars, trucks, RVs and bikes. Vehicles are checked in by VIN or plate, every visit stays on the vehicle's history, and fleets get unit numbers and account billing." },
    tools: { name: "Tools & small engine", words: ["tool", "tools", "drill", "saw", "chainsaw", "mower", "lawnmower", "generator", "engine", "small", "outdoor", "power", "trimmer", "blower"], page: "/small-engine-repair-software/",
      blurb: "The Tools & small engine template covers power tools, mowers, saws and generators. It captures voltage for tools and engine hours for small engines, and keeps warranty and paid jobs separate." },
    marine: { name: "Marine", words: ["boat", "boats", "marine", "outboard", "inboard", "hull", "yacht", "jetski", "vessel", "boatyard", "marina", "hin"], page: "/marine-repair-software/",
      blurb: "The Marine template covers outboards, inboards and hulls. It tracks the hull ID and each engine's serial separately, and handles seasonal work like winterising." },
  };

  /* ── Knowledge base: intents → answers. Every fact here is on the website. ── */
  const KB = [
    { id: "book", w: ["demo", "book", "booking", "meeting", "call", "schedule", "appointment", "walkthrough", "show", "talk", "sales", "person", "human", "someone"], a: () => ({ text: "Happy to set that up. A demo is 30 minutes: we set up your trade live, run one job from intake to paid, and answer questions about moving your data and pricing.", chips: ["Pick a time"], action: "book" }) },
    { id: "price", w: ["price", "prices", "pricing", "cost", "costs", "much", "expensive", "cheap", "plan", "plans", "pay", "fee", "monthly", "subscription", "per"], a: () => ({ text: `There are three plans:
• ${solo.name} — ${cur}${solo.monthly}/month: 2 team members, 150 new jobs a month.
• ${shop.name} — ${cur}${shop.monthly}/month for the first location and ${cur}${shop.extraLocation} for each extra: 4 team members shared across locations (${cur}${shop.extraSeat} a month per extra person), unlimited jobs, plus inventory, scanning, time clock and reports.
• ${pro.name} — ${cur}${pro.monthly}/month for the first location and ${cur}${pro.extraLocation} for each extra: 8 team members (${cur}${pro.extraSeat ?? 4} a month per extra person), unlimited jobs, plus purchase orders, payroll, AI estimates and accounting sync.
Yearly billing gets ${12 - ymo} months free. Every trial includes all Pro features for ${trial} days.`, chips: ["Which plan fits me?", "Book a demo"] }) },
    { id: "recommend", w: ["which", "recommend", "fit", "right", "best", "choose", "suit", "should"], a: () => ({ action: "recommend" }) },
    { id: "trial", w: ["trial", "free", "try", "test", "card", "credit"], a: () => ({ text: `Yes — every plan starts with a ${trial}-day free trial of all Pro features, no credit card needed. You can explore with sample jobs and clear them in one click.`, chips: ["Start free trial", "Book a demo"] }) },
    { id: "templates", w: ["template", "templates", "industry", "industries", "trade", "trades", "type", "kind", "support", "work"], a: () => ({ text: "Tekly has four templates: Electronics (phones, tablets, computers), Vehicles (cars, trucks, RVs, bikes), Tools & small engine, and Marine. Shops that fix more than one kind of thing can use several in one account. What do you repair?", chips: ["Phones & computers", "Cars & trucks", "Small engines", "Boats"] }) },
    { id: "ai", w: ["ai", "artificial", "intelligence", "automatic", "draft", "drafting", "smart", "gpt", "summary", "summaries"], a: () => ({ text: "Tekly AI is built into the screens you use: it drafts estimates from the customer's words, summarises jobs, suggests replies, turns supplier lists into parts, and tags call sentiment. It only drafts — a person on your team always presses send. Job summaries, reply suggestions and call insights are on every plan; estimate drafting and parts recognition come with Pro.", chips: ["Pricing", "Book a demo"] }) },
    { id: "approve", w: ["approve", "approval", "approvals", "sign", "signature", "estimate", "estimates", "quote", "quotes", "authorize", "consent"], a: () => ({ text: "Customers get a link by SMS, WhatsApp or email, approve each line of the estimate and sign on their phone. If you find more work mid-repair, they choose what to add — nothing is added unless they approve it.", chips: ["How do parts work?", "Book a demo"] }) },
    { id: "parts", w: ["parts", "part", "inventory", "stock", "purchase", "order", "orders", "po", "supplier", "warehouse", "barcode", "serial", "reorder"], a: () => ({ text: "Order parts from the work order. When they arrive and you scan them, they're written off to the right job and costed into its margin, and the customer's ready time updates. Inventory comes with Shop; purchase orders and multi-location transfers with Pro.", chips: ["Payroll", "Book a demo"] }) },
    { id: "payroll", w: ["payroll", "commission", "commissions", "wages", "staff", "technician", "technicians", "employees", "timeclock", "clock", "hours"], a: () => ({ text: "On Pro, technician commission and hours post automatically when a job closes, so the pay run is ready when you sit down. Pay runs can't pay twice, and there's a full audit log.", chips: ["Pricing", "Book a demo"] }) },
    { id: "pos", w: ["pos", "checkout", "sell", "sales", "retail", "payment", "payments", "stripe", "square", "invoice", "invoices", "receipt", "cash"], a: () => ({ text: "Tekly has a quick POS with cash drawers, invoices and payment links. Card payments go through Stripe or Square, and customers can pay online from their tracking page.", chips: ["Accounting sync", "Book a demo"] }) },
    { id: "accounting", w: ["quickbooks", "xero", "accounting", "accountant", "books", "bookkeeping", "ledger", "tax"], a: () => ({ text: "QuickBooks and Xero sync comes with Pro. Revenue, margin and payroll post to one ledger, so profit per job, technician and location is always current.", chips: ["Pricing", "Book a demo"] }) },
    { id: "messages", w: ["sms", "text", "texts", "whatsapp", "email", "gmail", "outlook", "notify", "notifications", "message", "messages", "inbox", "remind", "reminders"], a: () => ({ text: "Customer updates go out by SMS, WhatsApp or email (Gmail or Outlook), and replies land in one shared inbox. Customers also get a tracking link with live status, photos and the ready time.", chips: ["Approvals", "Book a demo"] }) },
    { id: "switch", w: ["switch", "switching", "migrate", "migration", "import", "move", "moving", "transfer", "repairdesk", "shopmonkey", "orderry", "jobber", "spreadsheet", "excel", "current"], a: () => ({ text: "Switching is free on every plan. Send us an export from your current system or spreadsheets and we bring your customers, devices and stock across. You can take your first job the day you sign up.", chips: ["Book a demo", "Start free trial"] }) },
    { id: "data", w: ["data", "export", "security", "secure", "safe", "privacy", "encrypt", "encryption", "gdpr", "leave", "cancel", "contract", "lock"], a: () => ({ text: "Your data is yours: export customers, jobs, stock and finances to CSV or Excel any time, even after you cancel. Passcodes and keys are encrypted, there's two-factor sign-in, role permissions and an audit log. Plans are month to month with no contract.", chips: ["Pricing", "Book a demo"] }) },
    { id: "mobile", w: ["mobile", "phone app", "app", "technician", "tech", "offline", "android", "iphone", "home", "install", "field"], a: () => ({ text: "Technicians get the Tekly Technician app: install it to the home screen from the browser, no app store. It shows today's jobs, runs the labour timer and checklists, scans parts with the camera, has a time clock, and keeps working offline.", chips: ["Book a demo", "Pricing"], link: { href: "/#mobile", label: "See the app" } }) },
    { id: "sense", w: ["sense", "learn", "learns", "suggest", "suggestion", "suggestions", "brief", "morning", "watchdog", "scorecard", "pattern", "upsell", "margin", "due"], a: () => ({ text: "Tekly Sense learns from your own closed jobs — no AI service, nothing shared between shops. It suggests lines from similar jobs, work usually added together, and services that are due, flags a job priced under your usual margin, and sends a Morning Brief. The owner also gets the Watchdog for unusual discounts, refunds and short drawers, plus a team scorecard.", chips: ["Pricing", "Book a demo"], link: { href: "/#sense", label: "See Tekly Sense" } }) },
    { id: "hardware", w: ["hardware", "printer", "scanner", "ipad", "tablet", "device", "install", "download", "app", "offline", "browser", "windows"], a: () => ({ text: "No special hardware — Tekly runs in any modern browser on a computer, tablet or phone, and works with standard barcode scanners, label printers and receipt printers.", chips: ["Book a demo"] }) },
    { id: "locations", w: ["locations", "location", "multi", "multiple", "branches", "stores", "franchise", "shops"], a: () => ({ text: `Solo covers one location. Shop adds locations at $49 a month each; Pro at $79 each, and adds stock transfers between them.`, chips: ["Which plan fits me?", "Book a demo"] }) },
    { id: "setup", w: ["setup", "onboarding", "start", "started", "long", "quick", "training", "learn"], a: () => ({ text: "You can take your first job the day you sign up, and onboarding is free on every plan. Pick your trade and the intake fields and services are pre-filled.", chips: ["Start free trial", "Book a demo"] }) },
    { id: "hello", w: ["hi", "hello", "hey", "hiya", "morning", "afternoon", "evening", "yo"], a: () => ({ text: "Hi! I can answer questions about Tekly, help you pick a plan, or book a 30-minute demo. What do you repair?", chips: ["Book a demo", "Which plan fits me?", "Pricing"] }) },
    { id: "thanks", w: ["thanks", "thank", "cheers", "great", "perfect", "awesome", "ok", "okay", "cool"], a: () => ({ text: "You're welcome! Anything else I can help with?", chips: ["Book a demo", "Pricing"] }) },
  ];

  /* ── Matching: normalise, light stemming, synonyms, typo tolerance ── */
  const SYN = { quote: "estimate", quotes: "estimate", ticket: "order", tickets: "order", wo: "order", dms: "messages", cost: "price", costs: "price", charge: "price", charges: "price", bucks: "price", dollars: "price", meet: "demo", zoom: "demo", walk: "demo", rep: "sales", agent: "human" };
  const norm = s => s.toLowerCase().replace(/[’']/g, "").replace(/[^a-z0-9$ ]+/g, " ").split(/\s+/).filter(Boolean)
    .map(w => SYN[w] || w).map(w => w.length > 4 ? w.replace(/(ing|ed|es|s)$/, "") : w);
  const lev = (a, b) => {
    if (Math.abs(a.length - b.length) > 1) return 2;
    const d = Array.from({ length: a.length + 1 }, (_, i) => [i]);
    for (let j = 1; j <= b.length; j++) d[0][j] = j;
    for (let i = 1; i <= a.length; i++) for (let j = 1; j <= b.length; j++)
      d[i][j] = Math.min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
    return d[a.length][b.length];
  };
  const stemmed = w => w.length > 4 ? w.replace(/(ing|ed|es|s)$/, "") : w;
  const hit = (tok, word) => { const w = stemmed(word); return tok === w || tok === word || (tok.length >= 5 && w.length >= 5 && lev(tok, w) <= 1); };
  const findTrade = toks => { for (const [k, t] of Object.entries(TRADES)) if (toks.some(x => t.words.some(w => hit(x, w)))) return k; return null; };
  // Generic words count half, so "do you sell pizza" doesn't land on the POS answer.
  const WEAK = new Set(["sell", "sales", "per", "work", "type", "kind", "which", "show", "talk", "call", "much", "current", "move", "moving", "start", "started", "long", "quick", "learn", "cash", "retail", "text", "message", "messages", "order", "orders", "books", "tax", "best", "right", "choose", "should", "fit", "suit", "lock", "leave", "app", "device", "browser", "install", "download", "pay", "free", "card", "try", "test", "smart", "automatic", "person", "someone", "hours", "clock", "staff", "data", "safe", "support", "shops", "stores", "multiple", "time"]);
  function classify(text) {
    const toks = norm(text);
    let best = null, score = 0;
    for (const it of KB) {
      let s = 0; for (const t of toks) { const m = it.w.find(w => hit(t, w)); if (m) s += WEAK.has(m) || WEAK.has(t) ? .5 : 1; }
      if (it.id === "hello" || it.id === "thanks") s *= toks.length <= 3 ? 1 : 0.3;
      if (s > score) { score = s; best = it; }
    }
    return { intent: score >= 1 ? best : null, trade: findTrade(toks), toks };
  }

  /* ── UI ── */
  const root = document.createElement("div");
  root.className = "ta";
  root.innerHTML = `
    <div class="ta-nudge" hidden><button class="ta-nudge-x" aria-label="Dismiss">×</button><p>Want to see Tekly with your own jobs? I can book a 30-minute demo.</p><button class="ta-nudge-go">Book a time</button></div>
    <button class="ta-launch" aria-label="Ask Tekly — questions and demo booking" aria-expanded="false" aria-controls="ta-panel"><svg viewBox="0 0 64 64" aria-hidden="true"><use href="#mark"/></svg><span>Ask Tekly</span></button>
    <section class="ta-panel" id="ta-panel" role="dialog" aria-label="${esc(A.name)}" hidden>
      <header class="ta-head"><span class="ta-av"><svg viewBox="0 0 64 64" aria-hidden="true"><use href="#mark"/></svg></span><div><b>${esc(A.name)}</b><span>Answers in seconds · books demos</span></div><button class="ta-x" aria-label="Close">×</button></header>
      <div class="ta-log" role="log" aria-live="polite"></div>
      <div class="ta-chips"></div>
      <form class="ta-form" autocomplete="off" novalidate><label class="sr" for="ta-in">Message</label><input id="ta-in" placeholder="Ask about pricing, your trade, a demo…" maxlength="300"><button type="submit" aria-label="Send"><svg viewBox="0 0 16 16"><path d="M2 8h11M9 4l4 4-4 4" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg></button></form>
      <p class="ta-foot">Automated assistant — no AI service, nothing stored beyond a booking.</p>
    </section>`;
  document.body.appendChild(root);
  const $ = s => root.querySelector(s);
  const panel = $(".ta-panel"), log = $(".ta-log"), chipsBox = $(".ta-chips"), input = $("#ta-in"), launch = $(".ta-launch"), nudge = $(".ta-nudge");

  const render = (m) => {
    const el = document.createElement("div");
    el.className = "ta-msg " + (m.who === "me" ? "me" : "bot");
    el.innerHTML = esc(m.text).replace(/\n/g, "<br>") + (m.link ? ` <a href="${esc(m.link.href)}">${esc(m.link.label)}</a>` : "");
    log.appendChild(el); log.scrollTop = log.scrollHeight;
  };
  const say = (text, opts = {}) => new Promise(res => {
    const t = document.createElement("div"); t.className = "ta-msg bot typing"; t.innerHTML = "<i></i><i></i><i></i>";
    log.appendChild(t); log.scrollTop = log.scrollHeight;
    setTimeout(() => { t.remove(); const m = { who: "bot", text, link: opts.link }; mem.msgs.push(m); save(); render(m); res(); }, reduced ? 0 : Math.min(900, 250 + text.length * 4));
  });
  const me = text => { const m = { who: "me", text }; mem.msgs.push(m); save(); render(m); };
  const chips = (list, handler) => {
    chipsBox.innerHTML = "";
    list.forEach(c => { const b = document.createElement("button"); b.type = "button"; b.className = "ta-chip"; b.textContent = typeof c === "string" ? c : c.label; b.onclick = () => { if (handler) { chipsBox.innerHTML = ""; handler(c); } else onUser(typeof c === "string" ? c : c.label); }; chipsBox.appendChild(b); });
  };

  let flow = null;   // active guided flow: { name, step, data }

  async function answer(r) {
    if (r.action === "book") return startBooking();
    if (r.action === "recommend") return startRecommend();
    await say(r.text, { link: r.link }); chips(r.chips || ["Book a demo", "Pricing"]);
  }

  async function onUser(text) {
    text = text.trim(); if (!text) return;
    me(text); chipsBox.innerHTML = "";
    if (flow && flow.onText) return flow.onText(text);
    const quick = { "Pick a time": () => startBooking(true), "Book a demo": () => startBooking(), "Book a time": () => startBooking(true), "Start free trial": () => goTrial(), "Pricing": () => answer(KB.find(k => k.id === "price").a()), "Which plan fits me?": () => startRecommend(), "Payroll": () => answer(KB.find(k => k.id === "payroll").a()), "Approvals": () => answer(KB.find(k => k.id === "approve").a()), "Accounting sync": () => answer(KB.find(k => k.id === "accounting").a()), "How do parts work?": () => answer(KB.find(k => k.id === "parts").a()),
      "Phones & computers": () => tradeAnswer("electronics"), "Cars & trucks": () => tradeAnswer("vehicles"), "Small engines": () => tradeAnswer("tools"), "Boats": () => tradeAnswer("marine") };
    if (quick[text]) return quick[text]();
    const { intent, trade, toks } = classify(text);
    if (/\bhow much\b|\bwhat does it cost\b|\bprice\b|\bpricing\b/i.test(text) && !(intent && intent.id === "price")) { if (trade) mem.trade = trade; return answer(KB.find(k => k.id === "price").a()); }
    if (toks.some(t => ["which", "recommend", "should", "best", "right", "fit", "suit", "choose", "pick"].includes(t)) && toks.some(t => ["plan", "plans", "price", "tier", "option", "package"].includes(t))) { if (trade) mem.trade = trade; return startRecommend(); }
    push("assistant_message", { intent: intent ? intent.id : (trade ? "trade" : "unknown") });
    if (trade && (!intent || intent.id === "templates" || intent.id === "hello")) return tradeAnswer(trade);
    if (trade) mem.trade = trade;
    if (intent) return answer(intent.a());
    await say("I'm not sure about that one — I'm an automated assistant and only know about Tekly's features, plans and demos. A person can answer it in a demo.");
    chips(["Book a demo", "Pricing", "Which plan fits me?"]);
  }

  async function tradeAnswer(k) {
    mem.trade = k; save();
    const t = TRADES[k];
    await say(t.blurb, { link: location.pathname === t.page ? null : { href: t.page, label: `More on ${t.name.toLowerCase()} repair` } });
    chips(["Which plan fits me?", "Book a demo", "Pricing"]);
  }

  function goTrial() {
    const a = document.querySelector('a[data-app="signup"]');
    push("cta_click", { cta: "assistant_trial" });
    if (a) location.href = a.href;
  }

  /* ── Plan recommender ── */
  async function startRecommend() {
    flow = { name: "rec", data: {} };
    await say("Two quick questions. How many locations do you run?");
    chips(["One", "Two or more"], async c => {
      me(c); mem.locations = c; save();
      flow.data.multi = c !== "One";
      await say("How many people will use Tekly, including you?");
      chips(["1–2", "3–4", "5 or more"], async d => {
        me(d);
        flow.data.big = d === "5 or more";
        await say("Do you need purchase orders, payroll and commissions, or QuickBooks/Xero sync?");
        chips(["Yes", "No"], async e => {
          me(e);
          if (e === "Yes") return recommend("pro", "Those come with Pro.");
          if (flow.data.big) return recommend("shop", `It includes 4 people shared across your locations, and each extra person is ${cur}${shop.extraSeat} a month. Pro includes 8 if you also need payroll or purchase orders.`);
          if (flow.data.multi) return recommend("shop", `Each extra location is ${cur}${shop.extraLocation} a month on Shop. If you need to move stock between locations, that comes with Pro.`);
          if (d === "3–4") return recommend("shop", "It covers up to 4 people with unlimited jobs, inventory, scanning, the time clock and reports.");
          await say("Do you keep parts in stock or need barcode labels?");
          chips(["Yes", "Not really"], f => { me(f); f === "Yes" ? recommend("shop", "Inventory, barcodes and scanning start on Shop.") : recommend("solo", "It covers 2 people and 150 new jobs a month, and you can move up any time."); });
        });
      });
    });
  }
  async function recommend(id, why) {
    flow = null;
    const p = id === "pro" ? pro : id === "shop" ? shop : solo;
    await say(`I'd go with ${p.name} — ${cur}${p.monthly} a month${p.extraLocation ? " for the first location" : ""}. ${why} Your ${trial}-day free trial includes every Pro feature, no card.`);
    push("assistant_recommend", { plan: id });
    chips(["Start free trial", "Book a demo", "Pricing"]);
  }

  /* ── Booking ── */
  const fmtDay = iso => new Date(iso + "T12:00:00").toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  const fmtTime = hm => { const [h, m] = hm.split(":").map(Number); const d = new Date(); d.setHours(h, m); return d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" }); };
  const api = async (path, opts) => { const r = await fetch(apiBase + path, opts); const j = await r.json().catch(() => ({})); if (!r.ok) throw Object.assign(new Error(j.detail || r.status), { status: r.status }); return j; };

  async function startBooking(skipIntro) {
    flow = { name: "book", data: { trade: mem.trade } };
    push("assistant_booking_start", { live: LIVE });
    if (!skipIntro) await say("Let's find a time for a 30-minute demo.");
    LIVE ? liveDays() : requestDays();
  }

  async function liveDays() {
    try {
      const j = await api(`/appointment/${encodeURIComponent(B.slug)}/days?days=21`);
      const open = (j.days || []).filter(d => d.open).slice(0, 6);
      if (!open.length) { await say("There are no open demo times in the next few weeks. I can pass on a request instead."); return requestDays(); }
      await say(`Which day suits you?${B.timezoneLabel ? ` Times are in ${B.timezoneLabel}.` : ""}`);
      chips(open.map(d => ({ label: fmtDay(d.date), date: d.date })), c => { me(c.label); flow.data.date = c.date; flow.data.dayLabel = c.label; liveSlots(); });
    } catch { await say("I couldn't load the calendar just now, so I'll take a request instead."); requestDays(); }
  }
  async function liveSlots() {
    try {
      const j = await api(`/appointment/${encodeURIComponent(B.slug)}/slots?date=${flow.data.date}`);
      const slots = (j.slots || []).map(s => typeof s === "string" ? s : s.time || s.start).filter(Boolean).slice(0, 8);
      if (!slots.length) { await say("That day just filled up — pick another?"); return liveDays(); }
      await say("And a time?");
      chips([...slots.map(s => ({ label: fmtTime(s), time: s })), { label: "Other day", back: true }], c => { me(c.label); if (c.back) return liveDays(); flow.data.time = c.time; flow.data.when = `${flow.data.dayLabel}, ${c.label}`; askDetails(); });
    } catch { await say("I couldn't load times just now, so I'll take a request instead."); requestDays(); }
  }

  async function requestDays() {
    const days = []; const d = new Date();
    while (days.length < 5) { d.setDate(d.getDate() + 1); if (d.getDay() % 6) days.push(new Date(d)); }
    await say("Which day works best? We'll confirm the exact time by email.");
    chips(days.map(x => ({ label: x.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" }) })), async c => {
      me(c.label); flow.data.dayLabel = c.label;
      await say("Morning or afternoon?");
      chips(["Morning", "Afternoon", "Either"], t => { me(t); flow.data.when = `${c.label}, ${t.toLowerCase()} (preferred)`; askDetails(); });
    });
  }

  const FIELDS = [
    { k: "name", q: "Great. What's your name?", ok: v => v.length >= 2, err: "Could you type your full name?" },
    { k: "email", q: "Thanks, {name}. What's the best email for the invite?", ok: v => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v), err: "That email doesn't look right — try again?" },
    { k: "shop", q: "And your shop's name?", ok: v => v.length >= 2, err: "What's the shop called?" },
  ];
  async function askDetails() {
    let i = 0;
    const ask = async () => {
      if (i >= FIELDS.length) return askTrade();
      const f = FIELDS[i];
      await say(f.q.replace("{name}", (flow.data.name || "").split(" ")[0]));
      input.type = f.k === "email" ? "email" : "text"; input.focus();
      flow.onText = async v => { if (!f.ok(v)) { await say(f.err); return; } flow.data[f.k] = v.slice(0, 120); i++; ask(); };
    };
    ask();
  }
  async function askTrade() {
    flow.onText = null; input.type = "text";
    if (flow.data.trade) return confirm();
    await say("What do you mainly repair?");
    chips([...Object.entries(TRADES).map(([k, t]) => ({ label: t.name, k })), { label: "More than one", k: "multi" }], c => { me(c.label); flow.data.trade = c.k; if (TRADES[c.k]) mem.trade = c.k; confirm(); });
  }
  async function confirm() {
    const d = flow.data, tradeName = TRADES[d.trade]?.name || "Several trades";
    await say(`Here's what I have:\n• ${d.when}\n• ${d.name} · ${d.email}\n• ${d.shop} · ${tradeName}`);
    chips(["Confirm", "Change time"], c => { me(c); c === "Confirm" ? submit() : (LIVE ? liveDays() : requestDays()); });
  }
  async function submit() {
    const d = flow.data, tradeName = TRADES[d.trade]?.name || "Several trades", attr = window.TEKLY_ATTRIBUTION || {};
    const note = `Website demo · Shop: ${d.shop} · Trade: ${tradeName}${mem.locations ? " · Locations: " + mem.locations : ""}${attr.utm_source ? " · Source: " + attr.utm_source + (attr.utm_campaign ? "/" + attr.utm_campaign : "") : ""}`;
    try {
      if (LIVE) {
        const r = await api(`/appointment/${encodeURIComponent(B.slug)}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: d.name, email: d.email, date: d.date, time: d.time, note }) });
        await say(`You're booked for ${r.when || d.when}. It's in our calendar, and we'll email ${d.email} with the details. Looking forward to it!`);
      } else {
        const lead = { name: d.name, email: d.email, shop: d.shop, industry: tradeName, preferred: d.when, locations: mem.locations };
        if (!C.demoEndpoint && !B.slug) {
          await say("I'll open an email with your request filled in — just press send.");
          await new Promise(r => setTimeout(r, 600));
        }
        const how = await window.TeklyLead.send(lead, "assistant");
        if (how === "sent") await say(`Thanks, ${d.name.split(" ")[0]} — request sent. We'll email ${d.email} to confirm a time on ${d.when}.`);
        else if (C.demoEndpoint || B.slug) await say("That didn't go through, so your email app opened with the request filled in — press send and we'll reply to find a time.");
      }
      if (LIVE) push("generate_lead", { form: "assistant", live: true, delivery: "booked", industry: tradeName, ...attr });
      flow = null; chips(["Start free trial", "Pricing"]);
    } catch (e) {
      if (e.status === 409) { await say("Someone just took that time — let's pick another."); return liveDays(); }
      await say(`Sorry, that didn't go through. You can email us at ${C.demoEmail} or try again.`); chips(["Book a demo"]);
    }
  }

  /* ── Open / close / nudge ── */
  const open = async (startFlow) => {
    panel.hidden = false; root.classList.add("open"); launch.setAttribute("aria-expanded", "true"); nudge.hidden = true; mem.open = true; mem.nudged = true; save();
    push("assistant_open", {});
    if (!log.childElementCount) {
      if (mem.msgs.length) mem.msgs.forEach(render);
      else { await say("Hi! I'm the Tekly assistant. I can answer questions, help you pick a plan, or book a 30-minute demo."); }
      if (!startFlow) chips(["Book a demo", "Which plan fits me?", "Pricing", "What do you support?"]);
    }
    if (startFlow) { me("Book a demo"); startBooking(); }
    setTimeout(() => input.focus(), 50);
  };
  const close = () => { panel.hidden = true; root.classList.remove("open"); launch.setAttribute("aria-expanded", "false"); mem.open = false; save(); launch.focus(); };
  launch.addEventListener("click", () => (panel.hidden ? open() : close()));
  $(".ta-x").addEventListener("click", close);
  root.addEventListener("keydown", e => { if (e.key === "Escape" && !panel.hidden) close(); });
  $(".ta-form").addEventListener("submit", e => { e.preventDefault(); const v = input.value; input.value = ""; onUser(v); });
  $(".ta-nudge-x").addEventListener("click", () => { nudge.hidden = true; mem.nudged = true; save(); });
  $(".ta-nudge-go").addEventListener("click", () => open(true));
  document.addEventListener("click", e => { const a = e.target.closest("[data-assistant]"); if (a) { e.preventDefault(); open(a.dataset.assistant === "book"); } });
  if (mem.open) open();
  if (!mem.nudged && !reduced) setTimeout(() => { if (panel.hidden && !mem.nudged) { nudge.hidden = false; mem.nudged = true; save(); } }, (A.nudgeAfterSec || 25) * 1000);
})();
