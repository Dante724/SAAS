/* ══════════════════════════════════════════════════════════════════════════
   Tekly marketing site — shared script for every page (built by tools/build_site.py
   and tools/build_pages.py). Each block runs only when its markup is on the page.

   Motion (Baseline): headings slide up word by word from behind a clip mask, blocks rise
   on a light spring, the hero photo drifts with the scroll, hovers spring. Each reveal
   plays once; nothing cycles on its own. Scrolling is the browser's own.
   ══════════════════════════════════════════════════════════════════════════ */
const C = window.TEKLY_SITE || {};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const root = document.documentElement;
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const canHover = () => matchMedia("(hover: hover) and (pointer: fine)").matches;
if (REDUCED) root.classList.add("reduced");
const cur = C.currency || "$";
const money = (n, dp = 0) => cur + Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const fmt = n => { const r = Math.round(n * 100) / 100; return r.toLocaleString("en-US", { minimumFractionDigits: Number.isInteger(r) ? 0 : 2, maximumFractionDigits: 2 }); };

/* ── Small spring for hover nudges and the demo form: v += (-k(x-target) - c·v)·dt ── */
class Spring {
  constructor(value, { tension = 170, friction = 26 } = {}, onUpdate = () => {}) {
    this.x = value; this.v = 0; this.target = value; this.k = tension; this.c = friction; this.onUpdate = onUpdate; this.raf = 0;
  }
  set(target, { immediate = false } = {}) {
    this.target = target;
    if (immediate || REDUCED) { cancelAnimationFrame(this.raf); this.raf = 0; this.x = target; this.v = 0; this.onUpdate(this.x); return; }
    if (!this.raf) { this.last = performance.now(); this.raf = requestAnimationFrame(this.tick); }
  }
  tick = now => {
    const dt = Math.min(0.064, (now - this.last) / 1000); this.last = now;
    for (let i = 0; i < 4; i++) { this.v += (-this.k * (this.x - this.target) - this.c * this.v) * dt / 4; this.x += this.v * dt / 4; }
    if (Math.abs(this.v) < 0.002 && Math.abs(this.x - this.target) < 0.002) { this.x = this.target; this.v = 0; this.raf = 0; this.onUpdate(this.x); return; }
    this.onUpdate(this.x); this.raf = requestAnimationFrame(this.tick);
  };
}

/* ── Content wiring: app links, contact email, trial length (from site.config.js) ── */
const appUrl = (C.appUrl || "").replace(/\/$/, "");
$$("[data-app]").forEach(a => { a.href = appUrl + (a.dataset.app === "signup" ? C.signUpPath || "/signup" : C.signInPath || "/login"); });
$$("[data-mail]").forEach(a => { a.href = "mailto:" + (C.demoEmail || ""); });
$$("[data-trial]").forEach(el => { el.textContent = C.trialDays ?? 14; });
$$("[data-save]").forEach(el => { el.textContent = `${12 - (C.yearlyMonths ?? 10)} months free`; });
$$("#yr").forEach(el => { el.textContent = new Date().getFullYear(); });

/* ── Scroll lock while the phone menu or the demo form is open ── */
let locks = 0;
const lock = () => { if (locks++ === 0) root.classList.add("is-locked"); };
const unlock = () => { if (locks > 0 && --locks === 0) root.classList.remove("is-locked"); };

/* ── Header: a hairline once the page scrolls; dropdowns; phone menu ── */
const hdr = $("#siteHdr");
if (hdr) {
  let on = null;
  const mark = () => { const s = scrollY > 4; if (s !== on) { on = s; hdr.classList.toggle("scrolled", s); } };
  addEventListener("scroll", mark, { passive: true }); mark();
}

const dds = $$("[data-dd]");
const closeDds = except => dds.forEach(d => { if (d === except) return; d.classList.remove("open"); $("button", d).setAttribute("aria-expanded", "false"); });
dds.forEach(d => {
  const btn = $("button", d), panel = $(".dd", d);
  let openedAt = 0;
  const open = () => { if (!d.classList.contains("open")) openedAt = performance.now(); closeDds(d); d.classList.add("open"); btn.setAttribute("aria-expanded", "true"); };
  const close = () => { d.classList.remove("open"); btn.setAttribute("aria-expanded", "false"); };
  // A mouse hover opens the menu just before the click lands; don't let that click close it again.
  btn.addEventListener("click", () => (d.classList.contains("open") && performance.now() - openedAt > 400 ? close() : open()));
  let t = 0;
  d.addEventListener("pointerenter", e => { if (e.pointerType === "mouse" && canHover()) { clearTimeout(t); open(); } });
  d.addEventListener("pointerleave", e => { if (e.pointerType === "mouse" && canHover()) { t = setTimeout(close, 160); } });
  d.addEventListener("keydown", e => {
    const links = $$("a", panel);
    if (e.key === "ArrowDown") { e.preventDefault(); if (!d.classList.contains("open")) open(); const i = links.indexOf(document.activeElement); links[Math.min(links.length - 1, i + 1)].focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); const i = links.indexOf(document.activeElement); (i <= 0 ? btn : links[i - 1]).focus(); }
  });
  d.addEventListener("focusout", e => { if (!d.contains(e.relatedTarget)) close(); });
});
document.addEventListener("click", e => { if (!e.target.closest("[data-dd]")) closeDds(); });

const sheet = $("#sheet"), menuBtn = $("[data-menu]");
let sheetOpen = false;
const openSheet = () => { if (sheetOpen || !sheet) return; sheetOpen = true; sheet.hidden = false; sheet.classList.add("open"); menuBtn.setAttribute("aria-expanded", "true"); menuBtn.setAttribute("aria-label", "Close menu"); lock(); };
const closeSheet = () => { if (!sheetOpen) return; sheetOpen = false; sheet.classList.remove("open"); sheet.hidden = true; menuBtn.setAttribute("aria-expanded", "false"); menuBtn.setAttribute("aria-label", "Open menu"); unlock(); };
if (sheet && menuBtn) {
  menuBtn.addEventListener("click", () => (sheetOpen ? closeSheet() : openSheet()));
  $$("a", sheet).forEach(a => a.addEventListener("click", closeSheet));
  addEventListener("resize", () => { if (innerWidth > 1080) closeSheet(); }, { passive: true });
}

/* ── Links to a section on the same page: the browser scrolls (CSS scroll-margin clears the header) ── */
document.addEventListener("click", e => {
  const a = e.target.closest('a[href*="#"]');
  if (!a || a.hasAttribute("data-app") || a.hasAttribute("data-mail") || a.hasAttribute("data-open-demo")) return;
  const u = new URL(a.href, location.href);
  if (u.origin !== location.origin || u.pathname !== location.pathname || u.hash.length < 2) return;
  const el = u.hash === "#top" ? null : document.querySelector(u.hash);
  e.preventDefault();
  if (el) el.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" }); else scrollTo({ top: 0, behavior: REDUCED ? "auto" : "smooth" });
  history.replaceState(null, "", u.hash);
});

/* ── Clip-mask reveals. Words are wrapped in place, so the text stays in the HTML for search. ── */
const EXPO = "cubic-bezier(.16, 1, .3, 1)";
const splitWords = el => {
  const walk = node => [...node.childNodes].forEach(n => {
    if (n.nodeType === 3) {
      const frag = document.createDocumentFragment();
      n.textContent.split(/(\s+)/).forEach(p => {
        if (!p) return;
        if (/^\s+$/.test(p)) { frag.appendChild(document.createTextNode(" ")); return; }
        const w = document.createElement("span"); w.className = "w";
        const i = document.createElement("span"); i.className = "wi"; i.textContent = p;
        w.appendChild(i); frag.appendChild(w);
      });
      n.replaceWith(frag);
    } else if (n.nodeType === 1 && n.tagName !== "BR" && !n.classList.contains("w") && !n.classList.contains("sr")) walk(n);
  });
  walk(el);
  return $$(".wi", el);
};
const revealInners = (inners, { stagger = 40, duration = 950, delay = 0, cap = 600 } = {}) => inners.forEach((s, i) => {
  if (REDUCED) return;
  s.animate([{ transform: "translateY(115%)", opacity: 0 }, { transform: "translateY(0)", opacity: 1 }],
    { duration, delay: delay + Math.min(i * stagger, cap), easing: EXPO, fill: "both" });
});
const fadeUp = (el, delay = 0, y = "1.5rem", duration = 900) => {
  if (REDUCED) return;
  el.animate([{ opacity: 0, transform: `translateY(${y})` }, { opacity: 1, transform: "none" }],
    { duration, delay, easing: "cubic-bezier(.2, 1.1, .3, 1)", fill: "both" });
};
const countUp = (el, to, ms = 900, delay = 0, dp = 2) => {
  const pre = el.dataset.prefix ?? (/^\$/.test(el.textContent) ? cur : "");
  const put = v => { el.textContent = pre + Number(v).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp }); };
  if (REDUCED) return put(to);
  put(0);
  setTimeout(() => { const t0 = performance.now(); const step = now => { const t = Math.min(1, (now - t0) / ms); put(to * (1 - Math.pow(1 - t, 4))); if (t < 1) requestAnimationFrame(step); }; requestAnimationFrame(step); }, delay);
};

/* ── In view, once: headings reveal by word; blocks rise, staggered with their siblings ── */
const io = new IntersectionObserver(es => es.forEach(e => {
  if (!e.isIntersecting) return;
  const el = e.target; io.unobserve(el);
  if (el.matches("[data-reveal]")) { revealInners(el._w || []); return; }
  const sibs = [...el.parentElement.children].filter(c => c.hasAttribute("data-rise"));
  el.style.setProperty("--rd", Math.min(Math.max(0, sibs.indexOf(el)), 5) * 80 + "ms");
  el.classList.add("is-in");
  $$("[data-count]", el).forEach(b => countUp(b, +b.dataset.count, 900, 150, 0));
}), { rootMargin: "0px 0px -6% 0px" });
$$("[data-reveal]").forEach(el => { el._w = splitWords(el); REDUCED || io.observe(el); });
$$("[data-rise]").forEach(el => (REDUCED ? el.classList.add("is-in") : io.observe(el)));
// Older page bodies (industry, compare, guide, legal) use .rv + .shown for the same thing.
const legacy = $$(".legacy .rv");
if (legacy.length) {
  const lio = new IntersectionObserver(es => es.forEach(x => { if (x.isIntersecting) { x.target.classList.add("shown"); lio.unobserve(x.target); } }), { rootMargin: "0px 0px 8% 0px" });
  legacy.forEach(el => (REDUCED ? el.classList.add("shown") : lio.observe(el)));
}

/* ── Hover: arrows nudge (pointer devices only) ── */
const bindHovers = (scope = document) => $$('[data-spring="nudge"]', scope).forEach(el => {
  if (el._sp) return; el._sp = 1;
  const t = $(".arr", el); if (!t) return;
  const s = new Spring(0, { tension: 300, friction: 20 }, x => { t.style.transform = `translateX(${x}px)`; });
  el.addEventListener("pointerenter", () => canHover() && s.set(4));
  el.addEventListener("pointerleave", () => s.set(0));
});
bindHovers();
// Cards lift a little under the pointer (pointer devices wider than 768px only)
$$(".tease, .new-card, .quote, .q-card, .tpl").forEach(el => {
  const s = new Spring(0, { tension: 300, friction: 22 }, y => { el.style.translate = `0 ${y}px`; });
  el.addEventListener("pointerenter", () => canHover() && innerWidth > 768 && s.set(-6));
  el.addEventListener("pointerleave", () => s.set(0));
});

/* ── Home hero: headline, tagline and the sample estimate play in on load ── */
const heroWords = $("[data-hero-words]");
if (heroWords) {
  const words = splitWords(heroWords);
  revealInners(words, { stagger: 140, duration: 1100, delay: 80, cap: 2000 });
  revealInners($$("#heroTag .lni"), { stagger: 110, duration: 900, delay: 350 });
  $$("[data-hero-fade]").forEach((el, i) => fadeUp(el, 250 + i * 120, "1rem", 800));
  const est = $("#heroEst");
  if (est) {
    fadeUp(est, 650, "1.75rem", 1000);
    $$("li", est).forEach((li, i) => fadeUp(li, 900 + i * 110, ".5rem", 600));
    fadeUp($(".gest-sum", est), 1350, ".5rem", 600);
    const b = $("[data-to]", est); if (b) countUp(b, +b.dataset.to, 1000, 1350);
  }
}

/* ── Parallax: the hero photo drifts down, ghost words slide apart. Geometry is measured on resize, not per frame. ── */
const plate = $("#heroPlate"), ghosts = $$("[data-ghost]");
if (!REDUCED && (plate || ghosts.length)) {
  let G = [], ticking = false;
  const measure = () => { G = [plate, ...ghosts].filter(Boolean).map(el => { const host = el === plate ? el.parentElement : el.closest("section"); const r = host.getBoundingClientRect(); return { el, top: r.top + scrollY, h: r.height }; }); };
  const frame = () => {
    ticking = false; const y = scrollY, vh = innerHeight;
    G.forEach(({ el, top, h }) => {
      if (y + vh < top || y > top + h) return;
      if (el === plate) { el.style.transform = `translate3d(0, ${Math.max(0, (y - top) / h) * 12}%, 0)`; return; }
      const p = (y + vh - top) / (vh + h) - .5;
      el.style.transform = `translate3d(${p * +el.dataset.ghost}rem, 0, 0)`;
    });
  };
  const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(frame); } };
  addEventListener("scroll", onScroll, { passive: true });
  addEventListener("resize", () => { measure(); onScroll(); }, { passive: true });
  addEventListener("load", () => { measure(); onScroll(); });
  measure(); frame();
}

/* ── Product: job stages (click a row to see its screen) ── */
const stepsBox = $("#steps");
if (stepsBox) {
  const TITLES = ["WO-1042 · Intake", "E-1042 · Estimate", "E-1042 · Approval", "PO-2208 · Parts", "Today · Paid"];
  const steps = $$(".row", stepsBox), scenes = $$(".scene"), screenTitle = $("#screenTitle");
  const PILLS = [["Checked in", "new"], ["Estimate draft", "progress"], ["Awaiting approval", "pending"], ["Waiting on parts", "pending"], ["Paid", "done"]];
  const pill = $("#jobPill"), stepper = $("#stepper");
  let stage = 0;
  const show = (i, focus) => {
    stage = (i + steps.length) % steps.length;
    steps.forEach((s, k) => { const on = k === stage; s.setAttribute("aria-selected", String(on)); s.tabIndex = on ? 0 : -1; });
    scenes.forEach((s, k) => {
      const was = !s.hidden; s.hidden = k !== stage;
      if (!s.hidden && !was && !REDUCED) { s.classList.remove("enter"); void s.offsetWidth; s.classList.add("enter"); }
    });
    const tot = $(".sum .r", scenes[stage]);
    if (tot && !tot._counted) { tot._counted = 1; countUp(tot, parseFloat(tot.textContent.replace(/[^\d.]/g, "")), 800, 250); }
    const [label, tone] = PILLS[stage];
    if (pill) { pill.dataset.tone = tone; $("span", pill).textContent = label; }
    $$("li", stepper).forEach((li, k) => { li.className = k < stage ? "is-done" : k === stage ? "is-cur" : ""; });
    screenTitle.textContent = TITLES[stage];
    if (focus) steps[stage].focus();
  };
  steps.forEach((s, i) => s.addEventListener("click", () => show(i)));

  /* The sample job is clickable like the app: checklist, estimate lines, the customer's approval, scanning the part */
  $$("[data-next]").forEach(b => b.addEventListener("click", () => show(stage + 1)));
  $$(".chk").forEach(b => b.addEventListener("click", () => { const on = b.classList.toggle("on"); b.setAttribute("aria-pressed", String(on)); }));
  const estTotal = $("#estTotal"), lines = $$("#estLines .line");
  lines.forEach(b => { b.setAttribute("aria-pressed", "true"); b.addEventListener("click", () => {
    const on = b.classList.toggle("on"); b.setAttribute("aria-pressed", String(on));
    estTotal.textContent = money(lines.filter(l => l.classList.contains("on")).reduce((t, l) => t + +l.dataset.amt, 0), 2);
  }); });
  const mrows = $$(".mini-phone .mrow"), mini = $("#miniApprove"), sign = $("#tlSign");
  let signed = false;
  const miniSum = () => mrows.filter(r => r.classList.contains("on")).reduce((t, r) => t + +r.dataset.amt, 0);
  mrows.forEach(r => { r.setAttribute("aria-pressed", String(r.classList.contains("on"))); r.addEventListener("click", () => {
    if (signed) return; const on = r.classList.toggle("on"); r.setAttribute("aria-pressed", String(on)); $("span", mini).textContent = money(miniSum());
  }); });
  mini?.addEventListener("click", () => {
    if (signed) { show(stage + 1); return; }
    signed = true; mini.classList.add("ok"); mini.textContent = "Approved ✓ · next: parts";
    sign.className = "is-done flash"; sign.innerHTML = `<b>Signed · ${money(miniSum())} approved</b><span>12:05 pm · declined lines saved for next visit</span>`;
    pill.dataset.tone = "progress"; $("span", pill).textContent = "In repair";
  });
  const scan = $("#poScan");
  scan?.addEventListener("click", () => {
    if (scan.classList.contains("ok")) { show(stage + 1); return; }
    const last = $("#poTrack li:last-child"); last.className = "is-done"; $("em", last).textContent = "now";
    const pp = $("#poPill"); pp.dataset.tone = "done"; $("span", pp).textContent = "Received";
    const ready = $("#poReady"); ready.textContent = "Today 4:00 pm"; ready.parentElement.classList.add("flash");
    pill.dataset.tone = "ready"; $("span", pill).textContent = "In repair · part in";
    scan.classList.add("ok"); scan.textContent = "Received ✓ · take payment";
  });
  // Desktop: the screen follows whichever step is in the middle of the view as you scroll
  if (matchMedia("(min-width: 1025px)").matches) {
    const sio = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) { const i = steps.indexOf(e.target); if (i !== stage) show(i); } }), { rootMargin: "-45% 0px -45% 0px" });
    steps.forEach(s => sio.observe(s));
  }
  stepsBox.addEventListener("keydown", e => {
    const d = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
    if (e.key === "Home" || e.key === "End") { e.preventDefault(); show(e.key === "Home" ? 0 : steps.length - 1, true); }
    else if (d) { e.preventDefault(); show(stage + d, true); }
  });
}

/* ── Product: the customer's approval phone ── */
const approveBtn = $("#approveBtn");
if (approveBtn) {
  const rows = $$("#heroPhone .p-row"), cap = $("#approveCap");
  let approved = false;
  const total = () => rows.filter(r => r.classList.contains("on")).reduce((s, r) => s + +r.dataset.amt, 0);
  const sync = () => { const a = $("#approveAmt"); if (a) a.textContent = money(total()); };
  const toggle = r => { if (approved || r.classList.contains("locked")) return; r.classList.toggle("on"); r.setAttribute("aria-checked", String(r.classList.contains("on"))); sync(); };
  rows.forEach(r => { r.addEventListener("click", () => toggle(r)); r.addEventListener("keydown", e => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggle(r); } }); });
  approveBtn.addEventListener("click", () => {
    if (approved) {
      approved = false; approveBtn.classList.remove("done");
      rows.forEach(r => { if (!r.classList.contains("locked")) { r.classList.remove("on"); r.setAttribute("aria-checked", "false"); } });
      approveBtn.innerHTML = `Approve <span class="tnum" id="approveAmt">${money(total())}</span>`;
      cap.textContent = "Tap the extras, then approve."; return;
    }
    approved = true; approveBtn.classList.add("done");
    approveBtn.textContent = `Approved ${money(total())} · reset`;
    cap.textContent = `The shop sees it instantly: WO-1042 → In repair · ${money(total(), 2)}`;
  });
}

/* ── Home: trade cards. A job-type chip shows that example job and its intake fields. ── */
const dl = f => f.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
$$(".tpl").forEach(card => {
  const list = $(".board-list", card); if (!list) return;
  const chips = $$(".cv", card), form = $(".tpl-form dl", card), formK = $(".tpl-form-k", card), count = $(".board-n", card);
  const jobs = [...list.children];
  chips.forEach((c, k) => c.addEventListener("click", () => {
    const job = jobs.find(j => +j.dataset.k === k); if (!job) return;
    list.prepend(job);
    chips.forEach((x, i) => { x.classList.toggle("on", i === k); x.setAttribute("aria-pressed", String(i === k)); });
    if (formK) formK.textContent = c.textContent;
    if (count) count.textContent = `${k + 1} of ${jobs.length}`;
    form.innerHTML = dl(JSON.parse(job.dataset.fields));
  }));
});

/* ── Home, below 1081px: the trade cards become a row you swipe or step through ── */
const car = $("#car"), track = $("#tradesGrid");
if (car && track) {
  const cards = $$(".tpl", track), carDots = $("#carDots"), prev = $("#carPrev"), next = $("#carNext");
  let cIdx = 0;
  const spring = new Spring(0, { tension: 220, friction: 28 }, x => { track.style.transform = `translate3d(${x}px,0,0)`; });
  const stepW = () => (cards.length > 1 ? cards[1].offsetLeft - cards[0].offsetLeft : 0);
  const visW = () => car.clientWidth - parseFloat(getComputedStyle(car).paddingLeft) * 2;
  const maxIdx = () => { const s = stepW(); return s ? Math.max(0, Math.ceil((track.scrollWidth - visW()) / s - 0.05)) : 0; };
  const goTo = (i, immediate) => {
    const m = maxIdx(); cIdx = Math.max(0, Math.min(m, i));
    spring.set(-Math.min(cIdx * stepW(), Math.max(0, track.scrollWidth - visW())), { immediate });
    if (prev) prev.disabled = cIdx === 0;
    if (next) next.disabled = cIdx === m;
    $$("button", carDots).forEach((b, k) => b.setAttribute("aria-current", String(k === cIdx)));
  };
  const renderDots = () => {
    if (!carDots) return;
    carDots.innerHTML = Array.from({ length: maxIdx() + 1 }, (_, i) => `<button type="button" aria-label="Go to ${$("h3", cards[i]).textContent}"></button>`).join("");
    $$("button", carDots).forEach((b, i) => b.addEventListener("click", () => goTo(i)));
  };
  prev?.addEventListener("click", () => goTo(cIdx - 1));
  next?.addEventListener("click", () => goTo(cIdx + 1));
  let sx = 0, sy = 0, base = 0, dragging = false, axis = null;
  car.addEventListener("pointerdown", e => { if (e.target.closest("a,button")) return; sx = e.clientX; sy = e.clientY; base = spring.x; dragging = true; axis = null; });
  addEventListener("pointermove", e => {
    if (!dragging) return;
    const dx = e.clientX - sx, dy = e.clientY - sy;
    if (!axis) { if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return; axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y"; }
    if (axis === "x") spring.set(base + dx, { immediate: true });
  }, { passive: true });
  addEventListener("pointerup", e => { if (!dragging) return; dragging = false; if (axis !== "x") return; const dx = e.clientX - sx; goTo(cIdx + (dx < -50 ? 1 : dx > 50 ? -1 : 0)); });
  const sync = () => { renderDots(); goTo(cIdx, true); $("#industries")?.classList.toggle("car-static", maxIdx() === 0); };
  sync(); addEventListener("resize", sync, { passive: true });
}

/* ── Product tabs: highlight the section on screen ── */
const subnav = $("[data-subnav]");
if (subnav) {
  const tabs = $$("a", subnav), targets = tabs.map(a => document.querySelector(a.getAttribute("href"))).filter(Boolean);
  const setCur = id => tabs.forEach(a => {
    const on = a.getAttribute("href") === "#" + id; a.setAttribute("aria-current", String(on));
    if (on && subnav.scrollWidth > subnav.clientWidth) subnav.scrollTo({ left: a.offsetLeft - subnav.clientWidth / 2 + a.offsetWidth / 2, behavior: "auto" });
  });
  const tio = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) setCur(e.target.id); }), { rootMargin: "-40% 0px -55% 0px" });
  targets.forEach(t => tio.observe(t));
}

/* ── Pricing: plans from site.config.js, monthly / yearly, two-location totals ── */
const plansBox = $("#plans");
if (plansBox) {
  let billing = "monthly";
  const render = () => {
    const ym = C.yearlyMonths ?? 10, yearly = billing === "annual", f = yearly ? ym : 1;
    // Yearly shows what it works out to each month, with the yearly bill and the saving in dollars underneath.
    const pm = n => (yearly ? (n * ym) / 12 : n);
    const each = yearly ? " a month" : "", billed = yearly ? ", billed yearly" : "";
    plansBox.innerHTML = (C.plans || []).map(p => {
      const extra = (p.extraLocation ? `<p class="extra">+ ${cur}${fmt(pm(p.extraLocation))}${each} for each extra location${billed}</p>` : "")
        + (p.extraSeat ? `<p class="extra">+ ${cur}${fmt(pm(p.extraSeat))}${each} for each extra team member${billed}</p>` : "");
      const saving = yearly ? `<p class="saving">Billed ${cur}${fmt(p.monthly * ym)} a year · you save ${cur}${fmt(p.monthly * (12 - ym))}</p>` : "";
      return `<article class="plan${p.featured ? " is-featured" : ""}">
        <div class="plan-top"><h3>${p.name}</h3>${p.badge ? `<span class="badge">${p.badge}</span>` : ""}</div>
        <p class="tagl">${p.tagline}</p>
        <div class="price"><span class="c">${cur}</span><span class="a">${fmt(pm(p.monthly))}</span>${yearly ? `<span class="was">${cur}${fmt(p.monthly)}</span>` : ""}</div>
        <p class="unit">${p.unit}</p>
        ${saving}
        ${extra}
        <p class="seats">${p.seats}</p>
        <a class="btn btn-wide ${p.featured ? "btn-solid" : "btn-outline"}" href="${appUrl}${C.signUpPath || "/signup"}?plan=${p.id}&billing=${yearly ? "year" : "month"}" data-spring="nudge">${p.cta} <svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a>
        <ul>${p.features.map(x => `<li><svg aria-hidden="true"><use href="#check"/></svg>${x}</li>`).join("")}</ul>
      </article>`;
    }).join("");
    const shop = (C.plans || []).find(x => x.id === "shop"), pro = (C.plans || []).find(x => x.id === "pro");
    const eg = $("#multiEg"), per = yearly ? "a year" : "a month";
    if (eg && shop && pro) eg.innerHTML =
      `<div><b>Two locations on Shop: ${cur}${fmt((shop.monthly + shop.extraLocation) * f)} ${per}</b><span>${cur}${fmt(shop.monthly * f)} + ${cur}${fmt(shop.extraLocation * f)}, with 4 team members shared between them. Inventory, scanning, time clock and reports.</span></div>`
      + `<div><b>Two locations on Pro: ${cur}${fmt((pro.monthly + pro.extraLocation) * f)} ${per}</b><span>${cur}${fmt(pro.monthly * f)} + ${cur}${fmt(pro.extraLocation * f)}, with 8 team members shared between them. Adds stock transfers between locations, purchase orders and payroll.</span></div>`;
    const e = C.enterprise;
    if (e && $("#ent")) $("#ent").innerHTML = `<div><b>${e.name}</b><p>${e.text}</p></div><button class="btn btn-outline" type="button" data-open-demo>${e.cta}</button>`;
    bindHovers(plansBox);
  };
  $$(".bill button").forEach(b => b.addEventListener("click", () => {
    billing = b.dataset.bill;
    $$(".bill button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
    render();
  }));
  render();
}
document.dispatchEvent(new Event("tekly:links-ready"));

/* ── Demo form (a dialog) ── */
const modal = $("#demoModal");
if (modal) {
  document.body.appendChild(modal);
  const panel = $(".modal-panel", modal), modalBg = $(".modal-bg", modal);
  let open = false, opener = null;
  const s = new Spring(0, { tension: 260, friction: 26 }, p => {
    panel.style.opacity = Math.min(1, Math.max(0, p)); modalBg.style.opacity = Math.min(1, Math.max(0, p));
    panel.style.transform = `translateY(${(1 - p) * 16}px)`;
  });
  const show = btn => {
    if (open) return; open = true; opener = btn; lock();
    modal.hidden = false; modal.classList.add("open");
    s.set(0, { immediate: true }); requestAnimationFrame(() => s.set(1));
    setTimeout(() => (($("#demoBody").hidden ? $("#demoDone a") : $("#d-name")) || panel).focus(), 60);
  };
  const hide = () => {
    if (!open) return; open = false;
    s.set(0, { immediate: REDUCED }); setTimeout(() => { modal.classList.remove("open"); modal.hidden = true; unlock(); }, REDUCED ? 0 : 180);
    opener?.focus();
  };
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-open-demo]"); if (!b) return;
    e.preventDefault(); closeSheet(); show(b);
  });
  $$("[data-close-modal]", modal).forEach(b => b.addEventListener("click", hide));
  addEventListener("keydown", e => {
    if (e.key === "Escape") { if (open) hide(); else if (sheetOpen) { closeSheet(); menuBtn.focus(); } else closeDds(); }
    if (e.key === "Tab" && open) {
      const f = $$('a[href],button:not([disabled]),input,select,textarea', panel).filter(x => x.offsetParent !== null);
      if (!f.length) return;
      if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
      else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
    }
  });

  $("#d-ind")?.insertAdjacentHTML("beforeend", ["Electronics", "Vehicles", "Tools & small engine", "Marine", "More than one of these"].map(t => `<option>${t}</option>`).join(""));
  // With no endpoint and no booking calendar set up, requests go out through the visitor's email app: say so up front.
  if (!C.demoEndpoint && !(C.assistant && C.assistant.booking && C.assistant.booking.slug)) {
    const note = $("#mailNote"); if (note) note.hidden = false;
    const btn = $("#demoForm button[type=submit]"); if (btn) btn.textContent = "Continue in my email app";
  }

  /* Demo form → TeklyLead.send (marketing.js): demoEndpoint, else Tekly's Inquiries board, else email. */
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
    const data = Object.fromEntries(new FormData(form)), btn = $("button[type=submit]", form), label = btn.textContent;
    Object.assign(data, window.TEKLY_ATTRIBUTION || {});
    btn.disabled = true; btn.textContent = "Sending…";
    try {
      const how = await window.TeklyLead.send(data, "website");
      if (how === "mailto") { $("#demoDone h2").textContent = "Almost done — press send."; $("#demoDone .lede").textContent = `Your email app opened a message to ${C.demoEmail}. Send it and we'll reply to find a time.`; }
      $("#demoBody").hidden = true; $("#demoDone").classList.add("show"); $("#demoDone a")?.focus();
    } catch {
      btn.disabled = false; btn.textContent = label;
      alert("Sorry — that didn't send. Please email " + C.demoEmail);
    }
  });
}

/* The on-page assistant isn't needed for first paint: load it when the browser is idle. */
const loadAssistant = () => { const s = document.createElement("script"); s.src = "/assistant.js"; document.body.appendChild(s); };
("requestIdleCallback" in window) ? requestIdleCallback(loadAssistant, { timeout: 3000 }) : setTimeout(loadAssistant, 1500);
