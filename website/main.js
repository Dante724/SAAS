(() => {
  const C = window.TEKLY_SITE || {};
  const P = window.TEKLY_PHOTOS || {};
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const cur = C.currency || "$";
  const money = (n, dp = 0) => cur + Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const inView = (el, cb, threshold = .35) => new IntersectionObserver(([e]) => cb(e.isIntersecting), { threshold }).observe(el);

  /* ── Links & small config ── */
  const appUrl = (C.appUrl || "").replace(/\/$/, "");
  $$("[data-app]").forEach(a => { a.href = appUrl + (a.dataset.app === "signup" ? C.signUpPath || "/signup" : C.signInPath || "/login"); });
  $$("[data-mail]").forEach(a => { a.href = "mailto:" + (C.demoEmail || ""); });
  $$("[data-trial]").forEach(el => { el.textContent = C.trialDays ?? 14; });
  $$("[data-save]").forEach(el => { el.textContent = `−${Math.round((C.annualDiscount ?? .2) * 100)}%`; });
  $("#yr").textContent = new Date().getFullYear();

  /* ── Photos (WebP with srcset; credits collected for the footer) ── */
  const credits = new Map();
  function setPhoto(img, slot, sizes) {
    const p = P[slot]; if (!p) return;
    let i = 0;
    const next = () => {
      if (i >= p.src.length) return;
      const u = p.src[i++];
      if (/-1400\.webp$/.test(u)) { img.sizes = sizes || "(max-width: 767px) 100vw, 50vw"; img.srcset = `${u.replace("-1400", "-720")} 720w, ${u} 1400w`; }
      else img.removeAttribute("srcset");
      img.src = u;
    };
    img.onerror = next;
    img.onload = () => { if (p.credit) { credits.set(slot.replace(/2$/, ""), p.credit); renderCredits(); } };
    next();
  }
  function renderCredits() {
    const list = [...credits.values()];
    $("#credits").innerHTML = (list.length ? list.map(c => `<a href="${c.url}" target="_blank" rel="noopener">${c.text}</a> (<a href="${c.licenseUrl}" target="_blank" rel="noopener">${c.license}</a>${c.adapted ? ", cropped and colour-graded" : ""})`).join(" · ") + ". " : "") + "Other photography is illustrative.";
  }
  $$("img[data-photo]").forEach(img => setPhoto(img, img.dataset.photo));

  /* ── Nav ── */
  const nav = $("#nav"), burger = $(".burger"), heroCtas = $(".hero-ctas");
  const onScroll = () => {
    nav.classList.toggle("stuck", scrollY > 4);
    nav.classList.toggle("past-hero", heroCtas.getBoundingClientRect().bottom < 56);
  };
  addEventListener("scroll", onScroll, { passive: true }); onScroll();
  const closeMenu = () => { nav.classList.remove("open"); burger.setAttribute("aria-expanded", "false"); };
  burger.addEventListener("click", () => { const o = nav.classList.toggle("open"); burger.setAttribute("aria-expanded", String(o)); });
  $$("#mmenu a").forEach(a => a.addEventListener("click", closeMenu));
  addEventListener("keydown", e => { if (e.key === "Escape" && nav.classList.contains("open")) { closeMenu(); burger.focus(); } });
  const navLinks = $$(".nav-links a");
  const secObs = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    navLinks.forEach(a => a.classList.toggle("cur", a.getAttribute("href") === "#" + e.target.id));
  }), { rootMargin: "-45% 0px -50% 0px" });
  ["product", "industries", "ai", "pricing", "faq"].forEach(id => secObs.observe(document.getElementById(id)));

  /* ── Reveal on scroll ── */
  const rvObs = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { e.target.classList.add("shown"); rvObs.unobserve(e.target); } }), { rootMargin: "0px 0px -8% 0px" });
  $$(".rv").forEach((el, i) => { el.style.transitionDelay = (el.closest(".hero") ? i * 60 : 0) + "ms"; rvObs.observe(el); });

  /* ── Hero: complaint → AI estimate ── */
  const EX = [
    { q: "iPhone 14 Pro dropped in water, won't charge and the screen flickers", asset: "Apple iPhone 14 Pro", lines: [["Liquid-damage diagnostic", "Bench test, liquid indicators, board inspection", 49], ["Ultrasonic board clean", "Corrosion removal and dry-out", 65], ["Charging port flex", "OEM-grade part · 30 min labour", 89], ["Display assembly", "OEM-grade screen · 45 min labour", 279]] },
    { q: "Grinding noise when braking, car pulls to the left", asset: "2019 Honda CR-V EX", lines: [["Brake inspection and road test", "Pads, rotors, calipers, hoses", 59], ["Front pads and rotors", "Pair · 1.4 h labour", 289], ["Caliper slide service", "Clean and lubricate, front axle", 45]] },
    { q: "150hp outboard won't idle and stalls when shifting into gear", asset: "150 hp four-stroke outboard", lines: [["Engine diagnostic scan", "Fault codes and idle air control check", 120], ["Fuel system service", "Filters, separator, injector clean", 210], ["Idle air control valve", "Part and 0.8 h labour", 185], ["Sea trial", "Verify idle and shift", 75]] },
    { q: "E-bike cuts out on hills, battery drops from 60% to zero", asset: "Commuter e-bike, 500 Wh", lines: [["Battery capacity test", "Load test and cell balance check", 45], ["Controller diagnostic", "Error log and connector inspection", 35], ["Main harness connector", "Replace corroded connector", 38]] },
  ];
  const input = $("#probeInput"), lines = $("#probeLines"), total = $("#probeTotal"), stat = $("#probeStat"), asset = $("#probeAsset"), chips = $$(".chip");
  let run = 0, autoplay = true, cycle = null;
  const wait = ms => new Promise(r => setTimeout(r, reduced ? 0 : ms));
  const pick = text => {
    const t = text.toLowerCase();
    if (/brake|car|truck|grind|rotor|pad/.test(t)) return 1;
    if (/outboard|boat|marine|idle|engine|motor/.test(t)) return 2;
    if (/bike|battery|e-bike|ebike|bicycle/.test(t)) return 3;
    return 0;
  };
  async function draft(i, typed) {
    const id = ++run, ex = EX[i];
    chips.forEach((c, k) => c.classList.toggle("on", k === i));
    if (typed) {
      input.value = ""; input.classList.add("caret");
      for (const ch of ex.q) { if (id !== run) return; input.value += ch; await wait(18); }
    }
    input.classList.remove("caret");
    asset.textContent = ex.asset; stat.textContent = "Drafting…"; lines.innerHTML = ""; total.textContent = "—";
    await wait(350);
    let sum = 0;
    for (const [k, [name, sub, price]] of ex.lines.entries()) {
      if (id !== run) return;
      sum += price;
      lines.insertAdjacentHTML("beforeend", `<li><span class="n mono">${String(k + 1).padStart(2, "0")}</span><span>${name}<small>${sub}</small></span><span class="p">${money(price, 2)}</span></li>`);
      total.textContent = money(sum, 2);
      await wait(260);
    }
    stat.textContent = `${ex.lines.length} lines · from your catalog`;
  }
  const stopAuto = () => { autoplay = false; clearInterval(cycle); };
  chips.forEach((c, k) => c.addEventListener("click", () => { stopAuto(); draft(k, true); }));
  $("#probeGo").addEventListener("click", () => { stopAuto(); draft(pick(input.value), false); });
  input.addEventListener("keydown", e => { if (e.key === "Enter") { stopAuto(); draft(pick(input.value), false); } });
  input.addEventListener("focus", stopAuto);
  draft(0, !reduced);
  inView($("#probe"), on => {
    clearInterval(cycle);
    if (on && autoplay && !reduced) { let k = 0; cycle = setInterval(() => { k = (k + 1) % EX.length; draft(k, true); }, 7000); }
  });

  /* ── Dark band: job stages ── */
  const steps = $$(".step"), scenes = $$(".scene"), desc = $("#stepDesc"), title = $("#screenTitle");
  const STAGES = [
    ["WO-1042 · Intake", "Intake asks for exactly what your trade needs — IMEI, VIN or HIN — with condition photos and passcodes stored encrypted."],
    ["E-1042 · Estimate", "Tekly AI drafts labour and parts from the customer's words, using your own catalog and prices. You review and send."],
    ["E-1042 · Approval", "The customer approves line by line on their phone and signs. Declined work is saved for next time."],
    ["PO-2208 · Parts", "Scan the box when it arrives and the part is written off to the right job — and the customer's ready time updates."],
    ["Today · Paid", "Payment at the counter or by link. Revenue, margin and the technician's commission post the moment the job closes."],
  ];
  let stage = 0, stageTimer = null, stageStopped = false, t0 = 0, raf = 0;
  const STAGE_MS = 6500;
  const showStage = (i, focus) => {
    stage = (i + steps.length) % steps.length;
    steps.forEach((s, k) => { const on = k === stage; s.classList.toggle("on", on); s.setAttribute("aria-selected", String(on)); s.tabIndex = on ? 0 : -1; s.style.setProperty("--p", on && !stageStopped ? "0%" : "100%"); });
    scenes.forEach((s, k) => { s.hidden = k !== stage; });
    title.textContent = STAGES[stage][0]; desc.textContent = STAGES[stage][1];
    if (focus) steps[stage].focus();
    t0 = performance.now();
  };
  const tick = now => {
    if (stageStopped) return;
    const p = Math.min(1, (now - t0) / STAGE_MS);
    steps[stage].style.setProperty("--p", (p * 100).toFixed(1) + "%");
    if (p >= 1) showStage(stage + 1);
    raf = requestAnimationFrame(tick);
  };
  const stopStages = () => { stageStopped = true; cancelAnimationFrame(raf); steps.forEach(s => s.style.setProperty("--p", "100%")); };
  steps.forEach((s, i) => s.addEventListener("click", () => { stopStages(); showStage(i); }));
  $("#steps").addEventListener("keydown", e => {
    const d = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (e.key === "Home" || e.key === "End") { e.preventDefault(); stopStages(); showStage(e.key === "Home" ? 0 : steps.length - 1, true); }
    else if (d) { e.preventDefault(); stopStages(); showStage(stage + d, true); }
  });
  showStage(0);
  if (!reduced) inView($("#product"), on => { cancelAnimationFrame(raf); if (on && !stageStopped) { t0 = performance.now(); raf = requestAnimationFrame(tick); } }, .3);

  /* ── Feature: phone approval ── */
  const rows = $$("#heroPhone .p-row"), approveBtn = $("#approveBtn"), cap = $("#approveCap");
  let approved = false;
  const totalApproved = () => rows.filter(r => r.classList.contains("on")).reduce((s, r) => s + +r.dataset.amt, 0);
  const syncAmt = () => { const a = $("#approveAmt"); if (a) a.textContent = money(totalApproved()); };
  const toggleRow = r => {
    if (approved || r.classList.contains("locked")) return;
    r.classList.toggle("on"); r.setAttribute("aria-checked", String(r.classList.contains("on"))); syncAmt();
  };
  rows.forEach(r => {
    r.addEventListener("click", () => toggleRow(r));
    r.addEventListener("keydown", e => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleRow(r); } });
  });
  approveBtn.addEventListener("click", () => {
    if (approved) {   // second press resets the demo
      approved = false; approveBtn.classList.remove("done");
      rows.forEach(r => { if (!r.classList.contains("locked")) { r.classList.remove("on"); r.setAttribute("aria-checked", "false"); } });
      approveBtn.innerHTML = `Approve <span class="tnum" id="approveAmt">${money(totalApproved())}</span>`;
      cap.textContent = "Tap the extras, then approve.";
      return;
    }
    approved = true; approveBtn.classList.add("done");
    approveBtn.textContent = `Approved ${money(totalApproved())} · reset`;
    cap.textContent = `The shop sees it instantly: WO-1042 → In repair · ${money(totalApproved(), 2)}`;
  });

  /* ── Industries ── */
  const TRADES = [
    ["phone", "Phone & tablet", "Screens, batteries, boards and water damage, with IMEI checks.", "IMEI · storage · passcode"],
    ["computer", "Computer & laptop", "Diagnostics, upgrades and board-level work.", "Serial · CPU · RAM · OS"],
    ["tools", "Power tools", "Warranty and non-warranty jobs side by side.", "Brand · voltage · warranty"],
    ["small-engine", "Small engine", "Mowers, saws and generators, booked ahead of the season.", "Engine model · hours · fuel"],
    ["powersports", "Powersports", "ATVs, side-by-sides, jet skis and sleds.", "VIN · year · hours"],
    ["auto", "Auto", "Bays, inspections and approvals at the speed of your lifts.", "VIN · plate · mileage"],
    ["bike", "Bicycle & e-bike", "Tune-ups, builds and e-bike batteries.", "Frame size · battery serial"],
    ["motorcycle", "Motorcycle", "Services, tyres and track prep with full history.", "VIN · mileage · plate"],
    ["marine", "Marine", "Outboards, inboards and hulls, winterising to repower.", "HIN · engine serial · length"],
    ["fleet", "Fleet & truck", "Preventive maintenance across a whole fleet.", "Unit # · DOT # · odometer"],
    ["general", "General repair", "Audio, appliances, consoles and coffee machines.", "Brand · model · serial"],
  ];
  $("#tradesGrid").innerHTML = TRADES.map(([id, name, d, f]) => `<article class="trade"><div class="im"><img data-trade="${id}" alt="${name} repair" loading="lazy"></div><div class="tx"><h3>${name}</h3><p>${d}</p><p class="fields mono">${f}</p></div></article>`).join("")
    + `<article class="trade note"><h3>More than one trade?</h3><p>Use several templates in one account — each job gets the right fields.</p></article>`;
  $$("img[data-trade]").forEach(img => setPhoto(img, img.dataset.trade, "(max-width: 767px) 50vw, 25vw"));
  $("#d-ind").insertAdjacentHTML("beforeend", TRADES.map(t => `<option>${t[1]}</option>`).join("") + "<option>Several of these</option>");

  /* ── Pricing ── */
  let billing = "monthly";
  const fmt = n => { const r = Math.round(n * 100) / 100; return Number.isInteger(r) ? String(r) : r.toFixed(2); };
  const renderPlans = () => {
    const off = 1 - (C.annualDiscount ?? .2);
    $("#plans").innerHTML = (C.plans || []).map(p => {
      const price = billing === "annual" ? p.monthly * off : p.monthly;
      return `<article class="plan${p.featured ? " is-featured" : ""}">
        <div class="plan-top"><h3>${p.name}</h3>${p.badge ? `<span class="mono badge">${p.badge}</span>` : ""}</div>
        <p class="tag">${p.tagline}</p>
        <div class="price"><span class="c">${cur}</span><span class="a">${fmt(price)}</span>${billing === "annual" ? `<span class="was">${cur}${fmt(p.monthly)}</span>` : ""}</div>
        <p class="unit mono">${p.unit}${billing === "annual" ? ", billed yearly" : ""}</p>
        <p class="seats">${p.seats}</p>
        <a class="btn btn-lg wide ${p.featured ? "btn-brand" : "btn-line"}" href="${appUrl}${C.signUpPath || "/signup"}?plan=${p.id}&billing=${billing}">${p.cta}</a>
        <ul>${p.features.map(f => `<li><svg aria-hidden="true"><use href="#check"/></svg>${f}</li>`).join("")}</ul>
      </article>`;
    }).join("");
    const e = C.enterprise;
    if (e) $("#ent").innerHTML = `<p><b>${e.name}</b>${e.text}</p><a class="btn btn-quiet" href="#start" data-pane="demo">${e.cta}</a>`;
    bindPaneLinks();
  };
  $$(".bill button").forEach(b => b.addEventListener("click", () => {
    billing = b.dataset.bill;
    $$(".bill button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
    renderPlans();
  }));
  renderPlans();

  /* ── Get started tabs ── */
  const tabs = $$(".seg button");
  const openPane = id => {
    tabs.forEach(b => { const on = b.dataset.tab === id; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    $$(".pane").forEach(p => p.classList.toggle("on", p.id === "pane-" + id));
  };
  tabs.forEach(b => b.addEventListener("click", () => openPane(b.dataset.tab)));
  $(".seg").addEventListener("keydown", e => {
    const i = tabs.indexOf(document.activeElement); if (i < 0) return;
    const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key]; if (!d) return;
    e.preventDefault(); const b = tabs[(i + d + tabs.length) % tabs.length]; b.focus(); openPane(b.dataset.tab);
  });
  function bindPaneLinks() { $$("[data-pane]").forEach(a => { if (a._b) return; a._b = 1; a.addEventListener("click", () => openPane(a.dataset.pane)); }); }
  bindPaneLinks();

  /* ── Demo form ── */
  const form = $("#demoForm");
  const emailOk = v => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v);
  let tried = false;
  const validate = el => {
    const v = el.value.trim(), bad = el.required && (!v || (el.type === "email" && !emailOk(v)));
    el.classList.toggle("bad", bad); el.setAttribute("aria-invalid", String(bad));
    $("#" + el.id + "-err")?.classList.toggle("show", bad);
    return !bad;
  };
  $$("[required]", form).forEach(el => { el.addEventListener("blur", () => tried && validate(el)); el.addEventListener("input", () => el.classList.contains("bad") && validate(el)); });
  form.addEventListener("submit", async e => {
    e.preventDefault(); tried = true;
    const req = $$("[required]", form);
    if (!req.map(validate).every(Boolean)) { req.find(f => f.classList.contains("bad"))?.focus(); return; }
    const data = Object.fromEntries(new FormData(form)), btn = $("button[type=submit]", form);
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      if (C.demoEndpoint) {
        const r = await fetch(C.demoEndpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...data, source: "website" }) });
        if (!r.ok) throw new Error(r.status);
      } else {
        const body = Object.entries(data).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join("\n");
        location.href = `mailto:${C.demoEmail}?subject=${encodeURIComponent("Tekly demo request — " + data.shop)}&body=${encodeURIComponent(body)}`;
      }
      form.hidden = true; $("#demoFine").hidden = true; $("#demoDone").classList.add("show");
    } catch {
      btn.disabled = false; btn.textContent = "Try again";
      alert("Sorry — that didn't send. Please email " + C.demoEmail);
    }
  });
})();
