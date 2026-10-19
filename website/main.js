(() => {
  const C = window.TEKLY_SITE || {};
  const P = window.TEKLY_PHOTOS || {};
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  const desktop = () => innerWidth >= 1024;
  const money = (n, dp = 0) => (C.currency || "$") + Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
  const hasGSAP = !!(window.gsap && window.ScrollTrigger);

  /* ── Basics ── */
  const appUrl = (C.appUrl || "").replace(/\/$/, "");
  $$("[data-app]").forEach(a => { a.href = appUrl + (a.dataset.app === "signup" ? C.signUpPath || "/signup" : C.signInPath || "/login"); });
  $$("[data-mail]").forEach(a => { a.href = "mailto:" + (C.demoEmail || ""); });
  $$("[data-trial]").forEach(el => { el.textContent = C.trialDays ?? 14; });
  $$("[data-save]").forEach(el => { el.textContent = `Save ${Math.round((C.annualDiscount ?? .2) * 100)}%`; });
  $("#yr").textContent = new Date().getFullYear();

  /* ── Photos: first source that loads wins; credits collected for the footer ── */
  const credits = new Map();
  function setPhoto(img, slot, done) {
    const p = P[slot]; if (!p) return;
    let i = 0;
    const tryNext = () => {
      if (i >= p.src.length) return;
      const u = p.src[i++];
      if (/-1400\.webp$/.test(u)) { img.sizes = img.dataset.sizes || "(max-width: 767px) 100vw, 50vw"; img.srcset = `${u.replace("-1400", "-720")} 720w, ${u} 1400w`; }
      else img.removeAttribute("srcset");
      img.src = u;
    };
    img.onerror = tryNext;
    img.onload = () => {
      const isCC = !!p.credit && (img.currentSrc || img.src).includes(p.src[0].replace("-1400.webp", ""));
      const cr = img.parentElement && img.parentElement.querySelector("[data-credit],.credit");
      if (isCC) {
        credits.set(slot, p.credit);
        if (cr) cr.innerHTML = `Photo: <a href="${p.credit.url}" target="_blank" rel="noopener">${p.credit.text}</a>, <a href="${p.credit.licenseUrl}" target="_blank" rel="noopener">${p.credit.license}</a>${p.credit.adapted ? " · adapted" : ""}`;
      } else if (cr) cr.textContent = "";
      renderCredits();
      done && done();
    };
    tryNext();
  }
  function renderCredits() {
    const list = [...credits.values()];
    $("#credits").innerHTML = (list.length ? list.map(c => `<a href="${c.url}" target="_blank" rel="noopener">${c.text}</a> (<a href="${c.licenseUrl}" target="_blank" rel="noopener">${c.license}</a>${c.adapted ? ", cropped and colour-graded" : ""})`).join(" · ") + ". " : "") + "Other photography is illustrative.";
  }
  $$("img[data-photo]").forEach(img => setPhoto(img, img.dataset.photo));

  /* ── Nav: sticky, scroll progress, mobile menu ── */
  const nav = $("#nav"), burger = $(".burger"), prog = $("#progress");
  const aiSec = document.getElementById("ai"), heroCta = document.querySelector(".hero-cta");
  const onScroll = () => {
    nav.classList.toggle("stuck", scrollY > 8);
    const r = aiSec.getBoundingClientRect(); nav.classList.toggle("dark-under", r.top <= 40 && r.bottom >= 40);
    nav.classList.toggle("past-hero", heroCta.getBoundingClientRect().bottom < 60);
    const h = document.documentElement.scrollHeight - innerHeight;
    prog.style.transform = `scaleX(${h > 0 ? scrollY / h : 0})`;
  };
  addEventListener("scroll", onScroll, { passive: true }); onScroll();
  burger.addEventListener("click", () => { const o = nav.classList.toggle("open"); burger.setAttribute("aria-expanded", String(o)); });
  $$("#mmenu a").forEach(a => a.addEventListener("click", () => { nav.classList.remove("open"); burger.setAttribute("aria-expanded", "false"); }));
  addEventListener("keydown", e => { if (e.key === "Escape" && nav.classList.contains("open")) { nav.classList.remove("open"); burger.setAttribute("aria-expanded", "false"); burger.focus(); } });
  // highlight current section
  const links = $$(".nav-links a");
  const secObs = new IntersectionObserver(es => es.forEach(e => {
    if (!e.isIntersecting) return;
    const target = { flow: "platform", stories: "platform", calc: "platform", start: "pricing" }[e.target.id] || e.target.id;
    links.forEach(a => a.classList.toggle("cur", a.getAttribute("href") === "#" + target));
  }), { rootMargin: "-45% 0px -50% 0px" });
  $$("main > section, main > .trades").forEach(s => s.id && secObs.observe(s));

  const lenis = null; // native scrolling only — smooth-scroll hijacking felt laggy

  /* ── Scroll animations ── */
  // split headings into words
  $$("[data-split]").forEach(h => {
    const walk = node => {
      [...node.childNodes].forEach(n => {
        if (n.nodeType === 3) {
          const frag = document.createDocumentFragment();
          n.textContent.split(/(\s+)/).forEach(part => {
            if (!part) return;
            if (/^\s+$/.test(part)) { frag.appendChild(document.createTextNode(part)); return; }
            const w = document.createElement("span"); w.className = "w";
            const i = document.createElement("span"); i.textContent = part; w.appendChild(i); frag.appendChild(w);
          });
          n.replaceWith(frag);
        } else if (n.nodeType === 1) walk(n);
      });
    };
    walk(h);
  });

  if (hasGSAP && !reduced) {
    gsap.registerPlugin(ScrollTrigger);
    $$("[data-split]").forEach(h => {
      gsap.from($$(".w > span", h), { yPercent: 110, duration: 1, ease: "power4.out", stagger: .045, scrollTrigger: { trigger: h, start: "top 88%" } });
    });
    ScrollTrigger.batch(".rv", {
      start: "top 90%",
      onEnter: els => gsap.to(els, { opacity: 1, y: 0, duration: .9, ease: "power3.out", stagger: .07, overwrite: true }),
    });
    // hero composite: settle in, then drift on scroll
    gsap.from("#dash", { rotateY: -14, rotateX: 6, x: 60, opacity: 0, duration: 1.4, ease: "power3.out", delay: .2 });
    gsap.from("#heroPhone", { y: 80, opacity: 0, duration: 1.3, ease: "power3.out", delay: .45 });
    gsap.to("#dash", { yPercent: -6, ease: "none", scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: true } });
    gsap.to("#heroPhone", { yPercent: -16, ease: "none", scrollTrigger: { trigger: ".hero", start: "top top", end: "bottom top", scrub: true } });
    // photo parallax
    $$("[data-parallax]").forEach(img => gsap.fromTo(img, { yPercent: -8 }, { yPercent: 0, ease: "none", scrollTrigger: { trigger: img.parentElement, start: "top bottom", end: "bottom top", scrub: true } }));
    // pins and price cards
    $$(".pin").forEach(p => gsap.from(p, { y: 30, opacity: 0, duration: 1, ease: "power3.out", scrollTrigger: { trigger: p, start: "top 92%" } }));
  } else {
    $$(".rv").forEach(el => { el.style.opacity = 1; el.style.transform = "none"; });
  }

  /* ── Hero: interactive phone approval ── */
  const phone = $("#heroPhone");
  const rows = $$(".p-row", phone);
  const approveBtn = $("#approveBtn");
  let touched = false, approved = false;
  const total = () => rows.filter(r => r.classList.contains("on")).reduce((s, r) => s + +r.dataset.amt, 0);
  const sync = () => { $("#approveAmt").textContent = money(total()); };
  const toggle = r => {
    if (approved || r.classList.contains("locked")) return;
    r.classList.toggle("on"); r.setAttribute("aria-checked", String(r.classList.contains("on"))); sync();
  };
  rows.forEach(r => {
    r.addEventListener("click", () => { touched = true; toggle(r); });
    r.addEventListener("keydown", e => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); touched = true; toggle(r); } });
  });
  const approve = () => {
    if (approved) return; approved = true;
    const t = total();
    $("#sig").classList.add("go");
    setTimeout(() => {
      approveBtn.classList.add("done"); approveBtn.textContent = "Approved ✓";
      $("#toastAmt").textContent = money(t); $("#toast").classList.add("show");
      const st = $("#st1042"); st.className = "st prog"; st.textContent = "In repair";
      $("#amt1042").textContent = money(t, 2);
      const row = $("#row1042"); row.classList.add("flash"); setTimeout(() => row.classList.remove("flash"), 1600);
    }, 1100);
    setTimeout(() => { if (!touched) reset(); }, 7000);
  };
  const reset = () => {
    approved = false; approveBtn.classList.remove("done"); approveBtn.innerHTML = `Approve <span class="tnum" id="approveAmt">$368</span>`;
    $("#toast").classList.remove("show"); $("#sig").classList.remove("go");
    rows.forEach(r => { if (!r.classList.contains("locked")) { r.classList.remove("on"); r.setAttribute("aria-checked", "false"); } });
    const st = $("#st1042"); st.className = "st wait"; st.textContent = "Awaiting approval"; $("#amt1042").textContent = "$368.00";
  };
  approveBtn.addEventListener("click", () => { touched = true; approve(); });
  // self-demo once if the visitor hasn't touched it
  if (!reduced) setTimeout(() => { if (touched) return; toggle(rows[2]); setTimeout(() => { if (!touched) approve(); }, 1300); }, 3200);

  /* ── Flow: pinned scroll on desktop, tap/autoplay on mobile ── */
  const flow = $("#flowEl"), steps = $$(".step", flow), scenes = $$(".scene", flow);
  let cur = 0;
  const show = i => {
    cur = Math.max(0, Math.min(steps.length - 1, i));
    steps.forEach((s, k) => { s.classList.toggle("on", k === cur); k === cur ? s.setAttribute("aria-current", "step") : s.removeAttribute("aria-current"); });
    scenes.forEach((s, k) => s.classList.toggle("on", k === cur));
  };
  let flowST = null, timer = null;
  const setupFlow = () => {
    flowST && flowST.kill(); flowST = null; clearInterval(timer);
    if (false) {
      flowST = ScrollTrigger.create({
        trigger: flow, start: "center center", end: "+=" + innerHeight * 1.5, pin: "#flow", pinSpacing: true,
        onUpdate: self => {
          const f = self.progress * steps.length; const i = Math.min(steps.length - 1, Math.floor(f));
          if (i !== cur) show(i);
          steps[cur].style.setProperty("--p", Math.min(1, f - i).toFixed(3));
        },
      });
      steps.forEach((s, i) => s.onclick = () => { const st = flowST; const y = st.start + (st.end - st.start) * ((i + .05) / steps.length); lenis ? lenis.scrollTo(y) : scrollTo({ top: y, behavior: "smooth" }); });
    } else {
      steps.forEach(s => s.style.setProperty("--p", 1));
      steps.forEach((s, i) => s.onclick = () => { show(i); clearInterval(timer); });
      let stopped = false;
      new IntersectionObserver(([e]) => { clearInterval(timer); if (e.isIntersecting && !stopped && !reduced) timer = setInterval(() => show((cur + 1) % steps.length), 6000); }, { threshold: .35 }).observe(flow);
      ["focusin", "touchstart", "pointerdown"].forEach(ev => flow.addEventListener(ev, () => { stopped = true; clearInterval(timer); }, { passive: true }));
    }
  };
  setupFlow();
  let rz; addEventListener("resize", () => { clearTimeout(rz); rz = setTimeout(() => { setupFlow(); hasGSAP && ScrollTrigger.refresh(); }, 250); });

  /* roving-tabindex + arrow keys for a group of tab buttons */
  function roving(list, onPick) {
    list.addEventListener("keydown", e => {
      const btns = $$("button", list); const i = btns.indexOf(document.activeElement); if (i < 0) return;
      const n = { ArrowRight: i + 1, ArrowDown: i + 1, ArrowLeft: i - 1, ArrowUp: i - 1, Home: 0, End: btns.length - 1 }[e.key];
      if (n === undefined) return; e.preventDefault();
      const b = btns[(n + btns.length) % btns.length]; b.focus(); onPick(b);
    });
  }
  const syncRoving = list => $$("button", list).forEach(b => b.tabIndex = b.getAttribute("aria-selected") === "true" ? 0 : -1);

  /* ── Trades switcher ── */
  const TRADES = [
    { id: "phone", name: "Phone & tablet", asset: "Device", desc: "Screens, batteries, boards and water damage — with IMEI checks and passcodes stored encrypted.", fields: [["Brand / model", "Apple iPhone 14 Pro"], ["IMEI", "35 291811 402177 3"], ["Storage", "256 GB"], ["Passcode", "•••••• encrypted"]], svc: ["Screen replacement", "Battery", "Charging port", "Board repair", "Data recovery"] },
    { id: "computer", name: "Computer & laptop", asset: "Device", desc: "Diagnostics, upgrades and board-level work, with every spec captured at the counter.", fields: [["Model", "MacBook Air M2"], ["Serial", "C02FK3XYQ6L4"], ["RAM / storage", "16 GB · 512 GB"], ["Charger received", "Yes"]], svc: ["Diagnostics", "Keyboard", "SSD upgrade", "Liquid damage", "OS reinstall"] },
    { id: "tools", name: "Power tools", asset: "Tool", desc: "Warranty and non-warranty jobs side by side, with brand quick-picks and voltage on every ticket.", fields: [["Brand / model", "18 V hammer drill"], ["Serial", "2214 0938 7712"], ["Voltage", "18 V"], ["Warranty", "In warranty"]], svc: ["Brushes & motor", "Chuck", "Switch", "Battery test", "Warranty claim"] },
    { id: "small-engine", name: "Small engine", asset: "Equipment", desc: "Mowers, saws and generators — with engine model and hours, and seasonal tune-ups booked ahead.", fields: [["Equipment", "21\" push mower"], ["Engine model", "OHV 163 cc"], ["Hours", "212"], ["Fuel", "Petrol"]], svc: ["Tune-up", "Carburettor clean", "Blade sharpen", "Pull-cord", "Winter storage"] },
    { id: "powersports", name: "Powersports", asset: "Vehicle", desc: "ATVs, side-by-sides, jet skis and sleds — tracked by VIN, year and hours.", fields: [["Vehicle", "2022 ATV 570"], ["VIN", "4XAS…7J120314"], ["Hours", "318 h"], ["Registration", "ORV-55821"]], svc: ["Service", "Suspension", "Belt & clutch", "Winterise", "Pre-season check"] },
    { id: "auto", name: "Auto", asset: "Vehicle", desc: "Bays, inspections and approvals that move as fast as your lifts — by VIN, plate and mileage.", fields: [["Vehicle", "2019 Honda CR-V EX"], ["VIN", "2HKRW2H5…KH604117"], ["Plate", "8KXM 214"], ["Mileage", "61,240 mi"]], svc: ["Brakes", "Oil service", "Diagnostics", "Tyres", "Inspection"] },
    { id: "bike", name: "Bicycle & e-bike", asset: "Bike", desc: "Tune-ups, builds and e-bike batteries, with frame size and battery serial on every job.", fields: [["Bike", "Commuter e-bike"], ["Frame size", "M / 54 cm"], ["Battery serial", "BT-55102-9"], ["Mileage", "1,820 km"]], svc: ["Tune-up", "Wheel true", "Brake bleed", "Battery check", "Build"] },
    { id: "motorcycle", name: "Motorcycle", asset: "Vehicle", desc: "Services, tyres and track prep, with service history the rider can see in the portal.", fields: [["Motorcycle", "2020 café racer 900"], ["VIN", "SMT…AJ9312"], ["Mileage", "14,902 mi"], ["Plate", "MC 4471"]], svc: ["Major service", "Chain & sprockets", "Tyres", "Valve check", "Track prep"] },
    { id: "marine", name: "Marine", asset: "Vessel", desc: "Outboards, inboards and hulls — winterisation to repower, by HIN and engine serial.", fields: [["Vessel", "19' centre console"], ["HIN", "ABC12345D707"], ["Engine serial", "1B3L-401229"], ["Length", "19 ft"]], svc: ["Winterise", "Impeller", "Lower unit", "Repower", "Detailing"] },
    { id: "fleet", name: "Fleet & truck", asset: "Unit", desc: "Unit numbers, DOT details and preventive maintenance across a whole fleet, billed to the account.", fields: [["Unit #", "214 · day cab"], ["VIN", "3AKJ…LSKN4410"], ["DOT #", "2911047"], ["Odometer", "412,880 mi"]], svc: ["PM service", "DOT inspection", "Brakes & air", "Tyres", "Road call"] },
    { id: "general", name: "General repair", asset: "Item", desc: "Audio, appliances, consoles, coffee machines — anything with a serial number and a story.", fields: [["Item", "Espresso machine"], ["Brand / model", "Dual boiler"], ["Serial", "EM-2207-3381"], ["Condition", "Leaks when heating"]], svc: ["Diagnose", "Descale & seal", "Electronics", "Parts order", "Return"] },
  ];
  const tl = $("#tradeList");
  tl.innerHTML = TRADES.map((t, i) => `<button role="tab" aria-selected="${i === 0}" aria-controls="tradePanel" id="trade-${t.id}" tabindex="${i === 0 ? 0 : -1}" data-id="${t.id}" class="${i === 0 ? "on" : ""}">${t.name}<span class="c">${String(i + 1).padStart(2, "0")}</span></button>`).join("");
  const sel = $("#d-ind"); sel.insertAdjacentHTML("beforeend", TRADES.map(t => `<option>${t.name}</option>`).join("") + "<option>Several of these</option>");
  const imgA = $("#tvImg"), imgB = $("#tvImg2");
  let front = imgA, back = imgB, curTrade = null, tradeTimer = null, tradeTouched = false;
  const renderTrade = id => {
    const t = TRADES.find(x => x.id === id); if (!t || curTrade === id) return; curTrade = id;
    $$("button", tl).forEach(b => { const on = b.dataset.id === id; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); }); syncRoving(tl); $("#tradePanel").setAttribute("aria-labelledby", "trade-" + id);
    // crossfade photo
    back.classList.add("out");
    back.alt = t.name + " repair";
    const incoming = back;
    setPhoto(incoming, id, () => { if (curTrade !== id) return; incoming.classList.remove("out"); front.classList.add("out"); [front, back] = [incoming, front]; });
    // swap copy
    const sw = [$("#tvSwap"), $("#tvSvc")];
    sw.forEach(e => e.classList.add("out"));
    setTimeout(() => {
      $("#tvTag").textContent = t.name + " template";
      $("#tvTitle").textContent = t.name;
      $("#tvDesc").textContent = t.desc;
      $("#tvAsset").textContent = "Asset type: " + t.asset;
      $("#tvFields").innerHTML = t.fields.map(([k, v]) => `<div><span class="k">${k}</span><span class="v">${v}</span></div>`).join("");
      $("#tvSvc").innerHTML = t.svc.map(s => `<span>${s}</span>`).join("");
      sw.forEach(e => e.classList.remove("out"));
    }, curTrade && $("#tvTitle").textContent ? 220 : 0);
  };
  $$("button", tl).forEach(b => b.addEventListener("click", () => { tradeTouched = true; clearInterval(tradeTimer); renderTrade(b.dataset.id); }));
  roving(tl, b => { tradeTouched = true; clearInterval(tradeTimer); renderTrade(b.dataset.id); });
  ["pointerenter", "focusin"].forEach(ev => $(".switch").addEventListener(ev, () => { tradeTouched = true; clearInterval(tradeTimer); }));
  $$("[data-trade]").forEach(a => a.addEventListener("click", () => { tradeTouched = true; clearInterval(tradeTimer); renderTrade(a.dataset.trade); }));
  // initial (no fade delay)
  (() => { const t = TRADES[0]; curTrade = t.id; setPhoto(imgA, t.id); imgA.alt = t.name + " repair"; $("#tvTag").textContent = t.name + " template"; $("#tvTitle").textContent = t.name; $("#tvDesc").textContent = t.desc; $("#tvAsset").textContent = "Asset type: " + t.asset; $("#tvFields").innerHTML = t.fields.map(([k, v]) => `<div><span class="k">${k}</span><span class="v">${v}</span></div>`).join(""); $("#tvSvc").innerHTML = t.svc.map(s => `<span>${s}</span>`).join(""); })();
  // gentle auto-tour while in view, until the visitor picks one
  new IntersectionObserver(([e]) => {
    clearInterval(tradeTimer);
    if (e.isIntersecting && !tradeTouched && !reduced) tradeTimer = setInterval(() => {
      const i = TRADES.findIndex(t => t.id === curTrade); renderTrade(TRADES[(i + 1) % TRADES.length].id);
    }, 4200);
  }, { threshold: .4 }).observe($(".trade-view"));
  // preload trade photos after first paint
  addEventListener("load", () => setTimeout(() => TRADES.forEach(t => { const i = new Image(); setPhoto(i, t.id); }), 1200));

  /* ── AI: typing + line reveal, spotlight cards ── */
  const aiQ = $("#aiQ"), aiLines = $$("#aiEst [data-l]");
  const fullQ = aiQ.textContent;
  const runAI = () => {
    if (reduced) return;
    aiQ.textContent = ""; aiLines.forEach(l => { l.style.opacity = 0; l.style.transform = "translateY(6px)"; });
    let i = 0;
    const type = setInterval(() => {
      aiQ.textContent = fullQ.slice(0, ++i);
      if (i >= fullQ.length) {
        clearInterval(type);
        aiLines.forEach((l, k) => setTimeout(() => { l.style.transition = "opacity .4s, transform .4s"; l.style.opacity = 1; l.style.transform = "none"; }, 350 + k * 380));
      }
    }, 28);
  };
  let aiRan = false;
  new IntersectionObserver(([e]) => { if (e.isIntersecting && !aiRan) { aiRan = true; runAI(); } }, { threshold: .6 }).observe($("#aiEst"));
  $$(".ai-card").forEach(c => c.addEventListener("pointermove", e => {
    const r = c.getBoundingClientRect(); c.style.setProperty("--mx", (e.clientX - r.left) + "px"); c.style.setProperty("--my", (e.clientY - r.top) + "px");
  }));

  /* ── Calculator ── */
  const A = Object.assign({ adminMinutesPerJob: 9, approvalUpliftPct: 3, callsAvoidedPerJob: .6, minutesPerCall: 3 }, C.calculator || {});
  const ins = { jobs: $("#cJobs"), ticket: $("#cTicket"), rate: $("#cRate") };
  const animNum = (el, to, fmt) => {
    const from = +(el.dataset.v || 0); el.dataset.v = to;
    if (reduced || !hasGSAP) { el.textContent = fmt(to); return; }
    const o = { v: from }; gsap.to(o, { v: to, duration: .5, ease: "power2.out", onUpdate: () => el.textContent = fmt(o.v) });
  };
  const calc = () => {
    const j = +ins.jobs.value, t = +ins.ticket.value, r = +ins.rate.value;
    Object.values(ins).forEach(i => i.style.setProperty("--fill", ((i.value - i.min) / (i.max - i.min) * 100) + "%"));
    $("#oJobs").textContent = j.toLocaleString(); $("#oTicket").textContent = money(t); $("#oRate").textContent = money(r);
    const adminH = j * A.adminMinutesPerJob / 60, callH = j * A.callsAvoidedPerJob * A.minutesPerCall / 60;
    const admin = adminH * r, calls = callH * r, upsell = j * t * A.approvalUpliftPct / 100;
    animNum($("#oTotal"), admin + calls + upsell, v => money(Math.round(v)));
    $("#oHours").textContent = `${Math.round(adminH + callH)} hours of admin back each month`;
    $("#oAdmin").textContent = money(Math.round(admin)); $("#oUpsell").textContent = money(Math.round(upsell)); $("#oCalls").textContent = money(Math.round(calls));
  };
  $("#oAssume").textContent = `Illustrative estimate, not a guarantee. Our assumptions: ${A.adminMinutesPerJob} minutes of admin saved per job, ${A.approvalUpliftPct}% more approved work from line-by-line phone approvals, and ${A.callsAvoidedPerJob} status calls avoided per job at ${A.minutesPerCall} minutes each.`;
  Object.values(ins).forEach(i => i.addEventListener("input", calc)); calc();

  /* ── Migration counters ── */
  new IntersectionObserver(([e], o) => {
    if (!e.isIntersecting) return; o.disconnect();
    $$("#migrate .move").forEach((m, k) => {
      const bar = $(".bar i", m), cnt = $(".cnt", m), to = +cnt.dataset.to;
      setTimeout(() => {
        bar.style.transition = "width 1.6s cubic-bezier(.2,.7,.2,1)"; bar.style.width = "100%";
        if (reduced || !hasGSAP) { cnt.textContent = to.toLocaleString(); return; }
        const o = { v: 0 }; gsap.to(o, { v: to, duration: 1.6, ease: "power2.out", onUpdate: () => cnt.textContent = Math.round(o.v).toLocaleString() });
      }, k * 220);
    });
  }, { threshold: .4 }).observe($("#migrate"));

  /* ── Pricing ── */
  let billing = "monthly";
  const fmtPrice = n => { const r = Math.round(n * 100) / 100; return Number.isInteger(r) ? String(r) : r.toFixed(2); };
  const renderPlans = () => {
    const off = 1 - (C.annualDiscount ?? .2), cur = C.currency || "$";
    $("#plans").innerHTML = (C.plans || []).map(p => {
      const price = billing === "annual" ? p.monthly * off : p.monthly;
      return `<article class="plan ${p.featured ? "feat" : ""}">
        <div class="top"><h3>${p.name}</h3>${p.badge ? `<span class="tag">${p.badge}</span>` : ""}</div>
        <p class="tagline">${p.tagline}</p>
        <div class="price"><span class="c">${cur}</span><span class="a">${fmtPrice(price)}</span>${billing === "annual" ? `<span class="was">${cur}${fmtPrice(p.monthly)}</span>` : ""}</div>
        <div class="unit">${p.unit}${billing === "annual" ? ", billed yearly" : ""}</div>
        <p class="seats">${p.seats}</p>
        <a class="btn ${p.featured ? "btn-white" : "btn-secondary"}" href="${appUrl}${C.signUpPath || "/signup"}?plan=${p.id}&billing=${billing}">${p.cta} <svg class="arr"><use href="#arr"/></svg></a>
        <ul>${p.features.map(f => `<li><svg><use href="#check"/></svg>${f}</li>`).join("")}</ul>
      </article>`;
    }).join("");
    const e = C.enterprise;
    if (e) $("#ent").innerHTML = `<div><h3>${e.name}</h3><p>${e.text}</p></div><a class="btn btn-secondary" href="#start" data-pane="demo">${e.cta} <svg class="arr"><use href="#arr"/></svg></a>`;
    bindPaneLinks();
  };
  const bill = $(".bill"), knob = $(".knob", bill);
  const moveKnob = () => { const on = $("button.on", bill); knob.style.left = on.offsetLeft + "px"; knob.style.width = on.offsetWidth + "px"; };
  $$("button", bill).forEach(b => b.addEventListener("click", () => {
    billing = b.dataset.bill;
    $$("button", bill).forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
    moveKnob(); renderPlans();
    if (hasGSAP && !reduced) gsap.from(".plan .price", { y: -8, opacity: 0, duration: .35, stagger: .05, ease: "power2.out" });
  }));
  addEventListener("resize", moveKnob); document.fonts?.ready.then(moveKnob);
  renderPlans(); moveKnob();

  /* ── Get started tabs ── */
  const tabs = $$(".seg button");
  const openPane = id => {
    tabs.forEach(b => { const on = b.dataset.tab === id; b.classList.toggle("on", on); b.setAttribute("aria-selected", String(on)); b.tabIndex = on ? 0 : -1; });
    $$(".pane").forEach(p => p.classList.toggle("on", p.id === "pane-" + id));
  };
  tabs.forEach(b => b.addEventListener("click", () => openPane(b.dataset.tab)));
  roving($(".seg"), b => openPane(b.dataset.tab));
  function bindPaneLinks() { $$("[data-pane]").forEach(a => { if (a._b) return; a._b = 1; a.addEventListener("click", () => openPane(a.dataset.pane)); }); }
  bindPaneLinks();

  /* ── Demo form ── */
  const form = $("#demoForm");
  const emailOk = v => /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v);
  const validate = el => {
    const v = el.value.trim(), bad = el.required && (!v || (el.type === "email" && !emailOk(v)));
    el.classList.toggle("bad", bad); el.setAttribute("aria-invalid", String(bad));
    el.parentElement.querySelector(".err")?.classList.toggle("show", bad);
    return !bad;
  };
  let tried = false;
  $$("[required]", form).forEach(el => { el.addEventListener("blur", () => tried && validate(el)); el.addEventListener("input", () => el.classList.contains("bad") && validate(el)); });
  form.addEventListener("submit", async e => {
    e.preventDefault();
    tried = true; const req = $$("[required]", form);
    if (!req.map(validate).every(Boolean)) { req.find(f => f.classList.contains("bad"))?.focus(); return; }
    const data = Object.fromEntries(new FormData(form)); const btn = $("button[type=submit]", form);
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      if (C.demoEndpoint) {
        const r = await fetch(C.demoEndpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...data, source: "website" }) });
        if (!r.ok) throw new Error(r.status);
      } else {
        const body = Object.entries(data).filter(([, v]) => v).map(([k, v]) => `${k}: ${v}`).join("\n");
        location.href = `mailto:${C.demoEmail}?subject=${encodeURIComponent("Tekly demo request — " + data.shop)}&body=${encodeURIComponent(body)}`;
      }
      form.style.display = "none"; $("#demoFine").style.display = "none"; $("#demoDone").classList.add("show");
    } catch {
      btn.disabled = false; btn.textContent = "Try again";
      alert("Sorry — that didn't send. Please email " + C.demoEmail);
    }
  });

  hasGSAP && addEventListener("load", () => ScrollTrigger.refresh());
})();
