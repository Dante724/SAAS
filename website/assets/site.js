/* ══════════════════════════════════════════════════════════════════════════
   Motion: a tiny rAF spring helper (hovers, menu, modal, carousel), compositor
   (WAAPI) clip-mask and rise reveals, parallax, live tiles, product tabs.
   Shared by every page built by tools/build_site.py: each block below runs only when its
   section is on the page.
   ══════════════════════════════════════════════════════════════════════════ */
const C = window.TEKLY_SITE || {};
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const root = document.documentElement;
const REDUCED = matchMedia("(prefers-reduced-motion: reduce)").matches;
const canHover = () => matchMedia("(min-width: 769px) and (hover: hover)").matches;
if (REDUCED) root.classList.add("reduced");
const cur = C.currency || "$";
const money = (n, dp = 0) => cur + Number(n).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const EXPO = "cubic-bezier(.16, 1, .3, 1)";
const SPRINGY = "cubic-bezier(.22, 1.2, .36, 1)";   // a settled spring, run on the compositor

/* ── Spring: v += (-tension*(x-target) - friction*v)*dt ── */
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
    const steps = 4, h = dt / steps;
    for (let i = 0; i < steps; i++) { this.v += (-this.k * (this.x - this.target) - this.c * this.v) * h; this.x += this.v * h; }
    if (Math.abs(this.v) < 0.002 && Math.abs(this.x - this.target) < 0.002) { this.x = this.target; this.v = 0; this.raf = 0; this.onUpdate(this.x); return; }
    this.onUpdate(this.x); this.raf = requestAnimationFrame(this.tick);
  };
}
const easeOutExpo = t => (t >= 1 ? 1 : 1 - Math.pow(2, -10 * t));
const easeInOutCubic = t => (t < .5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
const tween = (ms, ease, onUpdate, delay = 0) => new Promise(res => {
  if (REDUCED) { onUpdate(1); return res(); }
  const t0 = performance.now() + delay;
  const f = now => { const t = Math.max(0, Math.min(1, (now - t0) / ms)); onUpdate(ease(t)); t < 1 ? requestAnimationFrame(f) : res(); };
  requestAnimationFrame(f);
});

/* ── Content wiring: app links, contact email, trial length (from site.config.js) ── */
const appUrl = (C.appUrl || "").replace(/\/$/, "");
$$("[data-app]").forEach(a => { a.href = appUrl + (a.dataset.app === "signup" ? C.signUpPath || "/signup" : C.signInPath || "/login"); });
$$("[data-mail]").forEach(a => { a.href = "mailto:" + (C.demoEmail || ""); });
$$("[data-trial]").forEach(el => { el.textContent = C.trialDays ?? 14; if (el.dataset.count) el.dataset.count = C.trialDays ?? 14; });
$$("[data-save]").forEach(el => { el.textContent = `${12 - (C.yearlyMonths ?? 10)} months free`; });
$$("#yr").forEach(el => { el.textContent = new Date().getFullYear(); });

/* ── Scroll lock (Lenis arrives a moment later and never blocks first paint) ── */
let lenis = null, locks = 0;
const lock = () => { if (locks++ === 0) { lenis?.stop(); root.classList.add("is-locked"); } };
const unlock = () => { if (locks > 0 && --locks === 0) { lenis?.start(); root.classList.remove("is-locked"); } };
if (!REDUCED) {
  import("lenis").then(({ default: Lenis }) => {
    lenis = new Lenis({ lerp: 0.15, smoothWheel: true, autoRaf: true });
    if (locks) lenis.stop();
  }).catch(() => {});   // blocked or offline → native scrolling
}
const scrollToHash = hash => {
  const el = hash === "#top" ? 0 : document.querySelector(hash);
  if (el === null) return;
  const off = el === 0 ? 0 : (document.querySelector("[data-subnav]") ? -120 : -12);   // clear the sticky product tabs
  if (lenis) lenis.scrollTo(el, { offset: off, duration: 0.9 });
  else if (el === 0) scrollTo({ top: 0, behavior: REDUCED ? "auto" : "smooth" });
  else el.scrollIntoView({ behavior: REDUCED ? "auto" : "smooth" });
};
document.addEventListener("click", e => {
  const a = e.target.closest('a[href*="#"]');
  if (!a || a.hasAttribute("data-app") || a.hasAttribute("data-mail") || a.hasAttribute("data-open-demo")) return;
  const u = new URL(a.href, location.href);
  if (u.origin !== location.origin || u.pathname !== location.pathname) return;   // another page: let it load
  const h = u.hash; if (h.length < 2) return;
  e.preventDefault();
  const go = () => { scrollToHash(h); history.replaceState(null, "", h); };
  if (a.hasAttribute("data-close-menu") && menuOpen) { closeMenu(false); setTimeout(go, 320); } else go();
});

/* ── Word splitting for clip-mask reveals (text stays in the DOM for SEO) ── */
const splitWords = el => {
  const walk = node => {
    [...node.childNodes].forEach(n => {
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
  };
  walk(el);
  return $$(".wi", el);
};
const revealInners = (inners, { stagger = 14, duration = 650, delay = 0, cap = 350, fadeIn = true } = {}) => inners.forEach((s, i) => {
  if (REDUCED) { s.style.transform = "none"; s.style.opacity = 1; return; }
  s.animate([{ transform: "translateY(115%)", opacity: fadeIn ? 0 : 1 }, { transform: "translateY(0)", opacity: 1 }],
    { duration, delay: delay + Math.min(i * stagger, cap), easing: EXPO, fill: "both" });
});
const rise = (el, delay = 0) => {
  if (REDUCED) { el.style.opacity = 1; el.style.transform = "none"; return; }
  el.animate([{ opacity: 0, transform: "translateY(1rem)" }, { opacity: 1, transform: "none" }],
    { duration: 650, delay, easing: SPRINGY, fill: "backwards" }).finished.then(() => { el.style.opacity = 1; el.style.transform = "none"; });
  el.style.opacity = 1; el.style.transform = "none";
};

/* ── In-view reveals (once), started just before they enter the viewport ── */
const io = new IntersectionObserver(entries => entries.forEach(e => {
  if (!e.isIntersecting) return;
  io.unobserve(e.target);
  const el = e.target;
  if (el.matches("[data-reveal]")) revealInners(el._inners || []);
  else {
    const sibs = [...el.parentElement.children].filter(c => c.hasAttribute("data-rise"));
    rise(el, Math.min(Math.max(0, sibs.indexOf(el)), 4) * 50);
  }
}), { rootMargin: "0px 0px 8% 0px" });
$$("[data-reveal]").forEach(el => { el._inners = splitWords(el); io.observe(el); });
$$("[data-rise]").forEach(el => io.observe(el));

/* ── Hover springs (off at ≤768px) ── */
const bindHover = el => {
  const kind = el.dataset.spring;
  let apply;
  if (kind === "nudge") { const t = $(".arr", el); if (!t) return; const s = new Spring(0, { tension: 300, friction: 18 }, x => t.style.transform = `translateX(${x}px)`); apply = on => s.set(on ? 5 : 0); }
  else if (kind === "lift") { const s = new Spring(0, { tension: 220, friction: 22 }, y => el.style.transform = `translateY(${y}px)`); apply = on => s.set(on ? -8 : 0); }
  else if (kind === "scale") { const s = new Spring(1, { tension: 240, friction: 22 }, v => el.style.transform = `scale(${v})`); apply = on => s.set(on ? 1.03 : 1); }
  else if (kind === "pop") { const svg = $("svg", el); const s = new Spring(0, { tension: 300, friction: 18 }, x => svg.style.transform = `translateX(${x * (el.id === "carPrev" ? -1 : 1)}px)`); apply = on => s.set(on ? 4 : 0); }
  else if (kind === "spin") { const svg = $("svg", el); const s = new Spring(0, { tension: 200, friction: 20 }, r => svg.style.transform = `rotate(${r}deg)`); apply = on => s.set(on ? 90 : 0); }
  if (!apply) return;
  el.addEventListener("pointerenter", () => canHover() && apply(true));
  el.addEventListener("pointerleave", () => apply(false));
  el.addEventListener("focus", () => canHover() && apply(true));
  el.addEventListener("blur", () => apply(false));
};
const bindHovers = (scope = document) => $$("[data-spring]", scope).forEach(el => { if (el._sp) return; el._sp = 1; bindHover(el); });
bindHovers();
$$(".new-card, .tease").forEach(card => {
  const ico = $(".ico", card); if (!ico) return; const s = new Spring(1, { tension: 260, friction: 18 }, v => ico.style.transform = `scale(${v})`);
  card.addEventListener("pointerenter", () => canHover() && s.set(1.15)); card.addEventListener("pointerleave", () => s.set(1));
});

/* ── Hero: glass estimate carousel (auto 3.8s, cross-fade) ── */
const EX = [
  { q: "iPhone 14 Pro dropped in water, won't charge and the screen flickers", asset: "Apple iPhone 14 Pro", lines: [["Liquid-damage diagnostic", 49], ["Ultrasonic board clean", 65], ["Charging port flex", 89], ["Display assembly", 279]] },
  { q: "Grinding noise when braking, car pulls to the left", asset: "2019 Honda CR-V EX", lines: [["Brake inspection and road test", 59], ["Front pads and rotors", 289], ["Caliper slide service", 45]] },
  { q: "150hp outboard won't idle and stalls when shifting into gear", asset: "150 hp four-stroke outboard", lines: [["Engine diagnostic scan", 120], ["Fuel system service", 210], ["Idle air control valve", 185], ["Sea trial", 75]] },
  { q: "E-bike cuts out on hills, battery drops from 60% to zero", asset: "Commuter e-bike, 500 Wh", lines: [["Battery capacity test", 45], ["Controller diagnostic", 35], ["Main harness connector", 38]] },
];
let gStart = () => {};
if ($("#gcar")) {
const gStage = $("#gcarStage"), gDots = $("#gcarDots"), gCount = $("#gcarCount");
gStage.innerHTML = EX.map((x, i) => `<div class="gslide${i ? "" : " on"}" role="group" aria-roledescription="slide" aria-label="${i + 1} of ${EX.length}">
  <p class="q">“${x.q}”</p><p class="asset">${x.asset}</p>
  <ol>${x.lines.map(([n, p]) => `<li><span>${n}</span><b>${money(p, 2)}</b></li>`).join("")}</ol>
  <div class="tot"><span>${x.lines.length} lines · from your catalog</span><b>${money(x.lines.reduce((s, l) => s + l[1], 0), 2)}</b></div></div>`).join("");
gDots.innerHTML = EX.map((_, i) => `<button type="button" aria-label="Example ${i + 1}" aria-current="${!i}"></button>`).join("");
let gIdx = 0, gTimer = 0, gPaused = false;
const gShow = i => {
  gIdx = (i + EX.length) % EX.length;
  $$(".gslide", gStage).forEach((s, k) => s.classList.toggle("on", k === gIdx));
  $$("button", gDots).forEach((b, k) => b.setAttribute("aria-current", String(k === gIdx)));
  gCount.textContent = `${String(gIdx + 1).padStart(2, "0")} / ${String(EX.length).padStart(2, "0")}`;
};
gStart = () => { clearInterval(gTimer); if (!REDUCED && !gPaused) gTimer = setInterval(() => gShow(gIdx + 1), 6000); };
$$("button", gDots).forEach((b, k) => b.addEventListener("click", () => { gShow(k); gPaused = true; clearInterval(gTimer); }));
$("#gcar").addEventListener("pointerenter", () => { gPaused = true; clearInterval(gTimer); });
$("#gcar").addEventListener("pointerleave", e => { if (e.pointerType !== "mouse") return; gPaused = false; gStart(); });
$("#gcar").addEventListener("focusin", () => { gPaused = true; clearInterval(gTimer); });
}

/* ── Scroll: parallax + floating bar. Geometry is measured on resize, never per frame. ── */
const hero = $("#top"), plate = $("#heroPlate"), ghostA = $("#ghostA"), ghostB = $("#ghostB"), ind = $("#industries"), minibar = $("#minibar");
const aboutImg = $("#aboutImg"), about = $("#about");
let G = {}, barOn = null, ticking = false;
const docTop = el => el.getBoundingClientRect().top + scrollY;
const measure = () => { G = { vh: innerHeight, heroH: hero ? hero.offsetHeight : 0,
  indT: ind ? docTop(ind) : 0, indH: ind ? ind.offsetHeight : 0, abT: about ? docTop(about) : 0, abH: about ? about.offsetHeight : 0 }; };
const frame = () => {
  ticking = false;
  const y = scrollY, { vh, heroH, indT, indH, abT, abH } = G;
  if (!REDUCED) {
    if (plate && y < heroH) plate.style.transform = `translate3d(0, ${(y / heroH) * 12}%, 0)`;
    if (ghostA && y + vh > indT && y < indT + indH) {
      const ip = (y + vh - indT) / (vh + indH);
      ghostA.style.transform = `translate3d(${(ip - .5) * -6}rem, 0, 0)`;
      ghostB.style.transform = `translate3d(${(ip - .5) * 6}rem, 0, 0)`;
    }
    if (aboutImg && y + vh > abT && y < abT + abH) aboutImg.style.transform = `translate3d(0, ${-((y + vh - abT) / (vh + abH)) * 13}%, 0)`;
  }
  const on = y > heroH - 80 && !menuOpen;
  if (on !== barOn) { barOn = on; minibar.classList.toggle("show", on); }
};
const onScroll = () => { if (!ticking) { ticking = true; requestAnimationFrame(frame); } };
addEventListener("scroll", onScroll, { passive: true });
addEventListener("resize", () => { measure(); onScroll(); }, { passive: true });
addEventListener("load", () => { measure(); onScroll(); });
measure();

/* ── Job stages: numbered rows ↔ screen; the progress bar is a CSS animation ── */
if ($("#steps")) {
const STAGE_TITLES = ["WO-1042 · Intake", "E-1042 · Estimate", "E-1042 · Approval", "PO-2208 · Parts", "Today · Paid"];
const stepsBox = $("#steps"), steps = $$(".row", stepsBox), scenes = $$(".scene"), screenTitle = $("#screenTitle");
let stage = 0;
const showStage = (i, focus) => {
  stage = (i + steps.length) % steps.length;
  steps.forEach((s, k) => { const on = k === stage; s.setAttribute("aria-selected", String(on)); s.tabIndex = on ? 0 : -1; });
  scenes.forEach((s, k) => { const was = s.hidden; s.hidden = k !== stage; if (!s.hidden && was && !REDUCED) s.animate([{ opacity: 0, transform: "translateY(.75rem)" }, { opacity: 1, transform: "none" }], { duration: 500, easing: EXPO }); });
  screenTitle.textContent = STAGE_TITLES[stage];
  if (focus) steps[stage].focus();
};
const stopStages = () => stepsBox.classList.add("stopped");
if (REDUCED) stopStages();
steps.forEach((s, i) => {
  s.addEventListener("click", () => { stopStages(); showStage(i); });
  $(".bar i", s).addEventListener("animationend", () => { if (!stepsBox.classList.contains("stopped")) showStage(stage + 1); });
});
stepsBox.addEventListener("keydown", e => {
  const d = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
  if (e.key === "Home" || e.key === "End") { e.preventDefault(); stopStages(); showStage(e.key === "Home" ? 0 : steps.length - 1, true); }
  else if (d) { e.preventDefault(); stopStages(); showStage(stage + d, true); }
});
new IntersectionObserver(([e]) => stepsBox.classList.toggle("paused", !e.isIntersecting), { threshold: .3 }).observe($("#flow"));
}

/* ── Phone approval demo ── */
if ($("#approveBtn")) {
const pRows = $$("#heroPhone .p-row"), approveBtn = $("#approveBtn"), cap = $("#approveCap");
let approved = false;
const totalApproved = () => pRows.filter(r => r.classList.contains("on")).reduce((s, r) => s + +r.dataset.amt, 0);
const syncAmt = () => { const a = $("#approveAmt"); if (a) a.textContent = money(totalApproved()); };
const toggleRow = r => { if (approved || r.classList.contains("locked")) return; r.classList.toggle("on"); r.setAttribute("aria-checked", String(r.classList.contains("on"))); syncAmt(); };
pRows.forEach(r => { r.addEventListener("click", () => toggleRow(r)); r.addEventListener("keydown", e => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); toggleRow(r); } }); });
approveBtn.addEventListener("click", () => {
  if (approved) {
    approved = false; approveBtn.classList.remove("done");
    pRows.forEach(r => { if (!r.classList.contains("locked")) { r.classList.remove("on"); r.setAttribute("aria-checked", "false"); } });
    approveBtn.innerHTML = `Approve <span class="tnum" id="approveAmt">${money(totalApproved())}</span>`;
    cap.textContent = "Tap the extras, then approve."; return;
  }
  approved = true; approveBtn.classList.add("done");
  approveBtn.textContent = `Approved ${money(totalApproved())} · reset`;
  cap.textContent = `The shop sees it instantly: WO-1042 → In repair · ${money(totalApproved(), 2)}`;
});
}

/* ── Product tabs: highlight the section on screen ── */
const subnav = $("[data-subnav]");
if (subnav) {
  const tabs = $$("a", subnav), targets = tabs.map(a => document.querySelector(a.getAttribute("href"))).filter(Boolean);
  const setCur = id => tabs.forEach(a => {
    const on = a.getAttribute("href") === "#" + id; a.setAttribute("aria-current", String(on));
    if (on && subnav.scrollWidth > subnav.clientWidth) subnav.scrollTo({ left: a.offsetLeft - subnav.clientWidth / 2 + a.offsetWidth / 2, behavior: REDUCED ? "auto" : "smooth" });
  });
  const tabIO = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) setCur(e.target.id); }), { rootMargin: "-45% 0px -50% 0px" });
  targets.forEach(t => tabIO.observe(t));
}

/* ── Stats count-up ── */
$$("[data-count]").forEach(el => {
  new IntersectionObserver(([e], obs) => {
    if (!e.isIntersecting) return; obs.disconnect();
    const to = +el.dataset.count, pre = el.dataset.prefix || "";
    tween(900, easeOutExpo, t => { el.textContent = pre + Math.round(to * t); }, 100);
  }, { threshold: .4 }).observe(el);
});

/* ── Industries: each card shows example jobs from that kind of shop. One card changes at a
      time (each about every 7s); the photo and the job change together; hovering or focusing a
      card pauses it; a chip jumps straight to its job. The intake form follows the job on top. ── */
if ($("#tradesGrid")) {
const dl = f => f.map(([k, v]) => `<div><dt>${k}</dt><dd>${v}</dd></div>`).join("");
const PER_CARD_MS = 7000;
const tradeCards = $$(".tpl").map(card => {
  const list = $(".board-list", card), chips = $$(".cv", card), form = $(".tpl-form dl", card), formK = $(".tpl-form-k", card);
  const imgs = $$(".tile img", card), dots = $$(".tile-dots i", card), count = $(".board-n", card), prog = $(".board-prog", card);
  const total = list.children.length;
  const st = { card, visible: false, hover: false, busy: null, photo: 0 };
  const restartProg = () => { if (!prog) return; prog.classList.remove("run"); void prog.offsetWidth; prog.classList.add("run"); };
  const sync = () => {
    const k = +list.firstElementChild.dataset.k;
    chips.forEach((c, i) => { c.classList.toggle("on", i === k); c.setAttribute("aria-pressed", String(i === k)); });
    formK.textContent = chips[k].textContent;
    if (count) count.textContent = `${k + 1} of ${total}`;
    form.classList.add("swap");
    setTimeout(() => { form.innerHTML = dl(JSON.parse(list.firstElementChild.dataset.fields)); form.classList.remove("swap"); }, 160);
  };
  const photoNext = () => {
    if (imgs.length < 2) return;
    const prev = imgs[st.photo]; st.photo = (st.photo + 1) % imgs.length; const next = imgs[st.photo];
    prev.classList.remove("on"); prev.classList.add("out"); next.classList.remove("out"); next.classList.add("on");
    dots.forEach((d, i) => d.classList.toggle("on", i === st.photo));
    setTimeout(() => prev.classList.remove("out"), 1100);
  };
  const step = (withPhoto = true) => new Promise(res => {
    const done = () => { list.appendChild(list.firstElementChild); sync(); res(); };
    if (withPhoto) photoNext();
    if (REDUCED) return done();
    list.animate([{ transform: "translateY(0)" }, { transform: `translateY(${-list.firstElementChild.offsetHeight}px)` }], { duration: 800, easing: EXPO }).finished.then(done);
  });
  st.advance = async (withPhoto) => { while (st.busy) await st.busy; st.busy = step(withPhoto); await st.busy; st.busy = null; restartProg(); };
  const pauseAll = on => $$(".board-prog").forEach(p => p.classList.toggle("paused", on));
  card.addEventListener("pointerenter", () => { st.hover = true; pauseAll(true); });
  card.addEventListener("pointerleave", () => { st.hover = false; pauseAll(false); });
  card.addEventListener("focusin", () => { st.hover = true; pauseAll(true); });
  card.addEventListener("focusout", () => { st.hover = false; pauseAll(false); });
  chips.forEach((c, k) => c.addEventListener("click", async () => {
    st.hover = true;
    let guard = 0;
    while (st.busy) await st.busy;
    while (+list.firstElementChild.dataset.k !== k && guard++ < total) await st.advance(false);
    setTimeout(() => { if (!card.matches(":hover")) st.hover = false; }, 9000);
  }));
  st.restartProg = restartProg; restartProg();
  return st;
});
// One shared beat: every 7s all cards change together in a quick left-to-right wave.
// Hovering or focusing any card pauses the whole set, so they never drift apart.
if (!REDUCED && tradeCards.length) {
  const section = $("#industries");
  let inView = false;
  new IntersectionObserver(([e]) => { inView = e.isIntersecting; }, { threshold: .25 }).observe(section);
  setInterval(() => {
    if (!inView || tradeCards.some(c => c.hover || c.busy)) return;
    tradeCards.forEach((c, i) => setTimeout(() => c.advance(true), i * 140));
  }, PER_CARD_MS);
}
/* ── Industries carousel (arrows, dots, swipe) ── */
const track = $("#tradesGrid"), cards = $$(".tpl", track), carDots = $("#carDots"), prev = $("#carPrev"), next = $("#carNext"), car = $("#car");
let cIdx = 0;
const trackSpring = new Spring(0, { tension: 190, friction: 26 }, x => { track.style.transform = `translate3d(${x}px,0,0)`; });
const stepW = () => cards.length > 1 ? cards[1].offsetLeft - cards[0].offsetLeft : 0;
const visW = () => car.clientWidth - parseFloat(getComputedStyle(car).paddingLeft) * 2;
const maxIdx = () => { const s = stepW(); return s ? Math.max(0, Math.ceil((track.scrollWidth - visW()) / s - 0.05)) : 0; };
const renderDots = () => {
  carDots.innerHTML = Array.from({ length: maxIdx() + 1 }, (_, i) => `<button type="button" aria-label="Go to ${$("h3", cards[i]).textContent}"></button>`).join("");
  $$("button", carDots).forEach((b, i) => b.addEventListener("click", () => goTo(i)));
};
const goTo = (i, immediate) => {
  const m = maxIdx(); cIdx = Math.max(0, Math.min(m, i));
  trackSpring.set(-Math.min(cIdx * stepW(), Math.max(0, track.scrollWidth - visW())), { immediate });
  prev.disabled = cIdx === 0; next.disabled = cIdx === m;
  $$("button", carDots).forEach((b, k) => b.setAttribute("aria-current", String(k === cIdx)));
};
prev.addEventListener("click", () => goTo(cIdx - 1));
next.addEventListener("click", () => goTo(cIdx + 1));
car.addEventListener("keydown", e => { if (e.target.closest("input,.cv")) return; if (e.key === "ArrowRight") goTo(cIdx + 1); if (e.key === "ArrowLeft") goTo(cIdx - 1); });
{
  let sx = 0, sy = 0, base = 0, dragging = false, axis = null;
  car.addEventListener("pointerdown", e => { if (e.target.closest("a,button")) return; sx = e.clientX; sy = e.clientY; base = trackSpring.x; dragging = true; axis = null; });
  addEventListener("pointermove", e => {
    if (!dragging) return;
    const dx = e.clientX - sx, dy = e.clientY - sy;
    if (!axis) { if (Math.abs(dx) < 6 && Math.abs(dy) < 6) return; axis = Math.abs(dx) > Math.abs(dy) ? "x" : "y"; }
    if (axis === "x") trackSpring.set(base + dx, { immediate: true });
  }, { passive: true });
  addEventListener("pointerup", e => { if (!dragging) return; dragging = false; if (axis !== "x") return; const dx = e.clientX - sx; goTo(cIdx + (dx < -50 ? 1 : dx > 50 ? -1 : 0)); });
}
const syncStatic = () => $("#industries").classList.toggle("car-static", maxIdx() === 0);
renderDots(); goTo(0, true); syncStatic();
addEventListener("resize", () => { renderDots(); goTo(cIdx, true); syncStatic(); }, { passive: true });
}

/* ── Pricing ── */
let billing = "monthly";
if ($("#plans")) {
const fmt = n => { const r = Math.round(n * 100) / 100; return r.toLocaleString("en-US", { minimumFractionDigits: Number.isInteger(r) ? 0 : 2, maximumFractionDigits: 2 }); };
const renderPlans = first => {
  const ym = C.yearlyMonths ?? 10;
  $("#plans").innerHTML = (C.plans || []).map(p => {
    const yearly = billing === "annual";
    const price = yearly ? p.monthly * ym : p.monthly;
    const extra = (p.extraLocation ? `<p class="extra">+ ${cur}${fmt(yearly ? p.extraLocation * ym : p.extraLocation)} for each extra location</p>` : "")
      + (p.extraSeat ? `<p class="extra">+ ${cur}${fmt(yearly ? p.extraSeat * ym : p.extraSeat)} for each extra team member</p>` : "");
    const unit = yearly ? p.unit.replace("per month", "per year") + ` · ${12 - ym} months free` : p.unit;
    return `<article class="plan${p.featured ? " is-featured" : ""}"${first ? " data-rise" : ""} data-spring="lift">
      <div class="plan-top"><h3>${p.name}</h3>${p.badge ? `<span class="badge">${p.badge}</span>` : ""}</div>
      <p class="tagl">${p.tagline}</p>
      <div class="price"><span class="c">${cur}</span><span class="a">${fmt(price)}</span>${yearly ? `<span class="was">${cur}${fmt(p.monthly * 12)}</span>` : ""}</div>
      <p class="unit">${unit}</p>
      ${extra}
      <p class="seats">${p.seats}</p>
      <a class="btn btn-wide ${p.featured ? "btn-light" : "btn-dark"}" href="${appUrl}${C.signUpPath || "/signup"}?plan=${p.id}&billing=${yearly ? "year" : "month"}" data-spring="nudge">${p.cta} <svg class="arr" aria-hidden="true"><use href="#arr"/></svg></a>
      <ul>${p.features.map(f => `<li><svg aria-hidden="true"><use href="#check"/></svg>${f}</li>`).join("")}</ul>
    </article>`;
  }).join("");
  const shop = (C.plans || []).find(x => x.id === "shop");
  if (shop) {
    const ym = C.yearlyMonths ?? 10, yr = billing === "annual", f = yr ? ym : 1;
    const eg = $("#multiEg"); if (eg) eg.innerHTML = `<b>Two locations on Shop:</b> ${cur}${fmt(shop.monthly * f)} + ${cur}${fmt(shop.extraLocation * f)} = ${cur}${fmt((shop.monthly + shop.extraLocation) * f)} a ${yr ? "year" : "month"}, with 8 team members shared between them.`;
  }
  const e = C.enterprise;
  if (e && $("#ent")) $("#ent").innerHTML = `<div><b>${e.name}</b><p>${e.text}</p></div><button class="btn btn-outline" type="button" data-open-demo>${e.cta}</button>`;
  bindHovers($("#plans"));
  if (first) $$("#plans [data-rise]").forEach(el => io.observe(el));
};
$$(".bill button").forEach(b => b.addEventListener("click", () => {
  billing = b.dataset.bill;
  $$(".bill button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-pressed", String(x === b)); });
  renderPlans(false);
}));
renderPlans(true);
}
document.dispatchEvent(new Event("tekly:links-ready"));

/* ── Fullscreen menu ── */
const menu = $("#menu"), menuBg = $(".menu-bg", menu), menuLinks = $$(".menu-links a", menu), menuBottom = $(".menu-bottom", menu);
let menuOpen = false, menuOpener = null;
const fade = (el, to, ms = 400) => { const from = +getComputedStyle(el).opacity; return tween(ms, easeOutExpo, t => { el.style.opacity = from + (to - from) * t; }); };
const openMenu = opener => {
  if (menuOpen) return; menuOpen = true; menuOpener = opener; lock();
  menu.hidden = false; menu.classList.add("open");
  $$("[data-open-menu]").forEach(b => b.setAttribute("aria-expanded", "true"));
  minibar.classList.remove("show"); barOn = false;
  fade(menuBg, 1, 350);
  menuLinks.forEach((a, i) => { a.style.opacity = 0; a.style.transform = "translateY(2rem)";
    const s = new Spring(0, { tension: 200, friction: 22 }, p => { a.style.opacity = Math.min(1, Math.max(0, p)); a.style.transform = `translateY(${(1 - p) * 2}rem)`; });
    setTimeout(() => s.set(1), REDUCED ? 0 : 120 + i * 70); });
  setTimeout(() => fade(menuBottom, 1, 450), REDUCED ? 0 : 120 + menuLinks.length * 50);
  setTimeout(() => $(".menu-x", menu).focus(), 50);
};
const closeMenu = (restoreFocus = true) => {
  if (!menuOpen) return; menuOpen = false;
  $$("[data-open-menu]").forEach(b => b.setAttribute("aria-expanded", "false"));
  menuLinks.forEach(a => fade(a, 0, 200)); fade(menuBottom, 0, 200);
  fade(menuBg, 0, 300).then(() => { menu.classList.remove("open"); menu.hidden = true; unlock(); onScroll(); });
  if (restoreFocus) menuOpener?.focus();
};
$$("[data-open-menu]").forEach(b => b.addEventListener("click", () => openMenu(b)));
$$("[data-close-menu]", menu).forEach(b => { if (!b.matches('a[href^="#"]')) b.addEventListener("click", () => closeMenu()); });
$$("a:not([href^='#'])", menu).forEach(a => a.addEventListener("click", () => closeMenu(false)));

/* ── Demo modal (portaled to <body>) ── */
const modal = $("#demoModal"); document.body.appendChild(modal);
const panel = $(".modal-panel", modal), modalBg = $(".modal-bg", modal);
let modalOpen = false, modalOpener = null;
const panelSpring = new Spring(0, { tension: 230, friction: 22 }, p => {
  panel.style.opacity = Math.min(1, Math.max(0, p));
  panel.style.transform = `translateY(${(1 - p) * 28}px) scale(${0.96 + 0.04 * p})`;
});
const openModal = opener => {
  if (modalOpen) return; modalOpen = true; modalOpener = opener; lock();
  modal.hidden = false; modal.classList.add("open");
  panelSpring.set(0, { immediate: true }); fade(modalBg, 1, 300);
  requestAnimationFrame(() => panelSpring.set(1));
  setTimeout(() => (($("#demoBody").hidden ? $("#demoDone a") : $("#d-name")) || panel).focus(), 60);
};
const closeModal = () => {
  if (!modalOpen) return; modalOpen = false;
  panelSpring.set(0); fade(modalBg, 0, 250).then(() => { modal.classList.remove("open"); modal.hidden = true; unlock(); });
  modalOpener?.focus();
};
document.addEventListener("click", e => {
  const b = e.target.closest("[data-open-demo]"); if (!b) return;
  e.preventDefault();
  if (menuOpen) { closeMenu(false); setTimeout(() => openModal(b), 250); } else openModal(b);
});
$$("[data-close-modal]", modal).forEach(b => b.addEventListener("click", closeModal));
addEventListener("keydown", e => {
  if (e.key === "Escape") { if (modalOpen) closeModal(); else if (menuOpen) closeMenu(); }
  if (e.key === "Tab" && (modalOpen || menuOpen)) {
    const box = modalOpen ? panel : menu;
    const f = $$('a[href],button:not([disabled]),input,select,textarea', box).filter(x => x.offsetParent !== null);
    if (!f.length) return;
    if (e.shiftKey && document.activeElement === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  }
});

$("#d-ind")?.insertAdjacentHTML("beforeend", ["Electronics", "Vehicles", "Tools & small engine", "Marine", "More than one of these"].map(t => `<option>${t}</option>`).join(""));

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
  const data = Object.fromEntries(new FormData(form)), btn = $("button[type=submit]", form);
  Object.assign(data, window.TEKLY_ATTRIBUTION || {});
  btn.disabled = true; btn.textContent = "Sending…";
  try {
    const how = await window.TeklyLead.send(data, "website");
    if (how === "mailto") { $("#demoDone h2").textContent = "Almost done — press send."; $("#demoDone .lede").textContent = `Your email app opened a message to ${C.demoEmail}. Send it and we'll reply to find a time.`; }
    $("#demoBody").hidden = true; $("#demoDone").classList.add("show"); $("#demoDone a")?.focus();
  } catch {
    btn.disabled = false; btn.textContent = "Try again";
    alert("Sorry — that didn't send. Please email " + C.demoEmail);
  }
});

/* ── Hero reveal (no splash loader: the headline is painted at once and animates in) ── */
const heroWordsEl = $("[data-hero-words]");
if (heroWordsEl) {
  const heroWords = splitWords(heroWordsEl), heroLines = $$("#heroTag .lni");
  revealInners(heroWords, { stagger: 45, duration: 600, delay: 0, cap: 300, fadeIn: false });
  revealInners(heroLines, { stagger: 40, duration: 600, delay: 100, fadeIn: false });
}
$$("[data-hero-fade]").forEach((el, i) => rise(el, 120 + i * 60));
const gcar = $("#gcar");
if (gcar) {
  if (REDUCED) { gcar.style.opacity = 1; gcar.style.transform = "none"; }
  else gcar.animate([{ opacity: 0, transform: "translateY(1.5rem)" }, { opacity: 1, transform: "none" }], { duration: 700, delay: 300, easing: SPRINGY, fill: "both" });
  setTimeout(gStart, 300);
}
root.classList.remove("is-loading");
onScroll();

/* The on-page assistant isn't needed for first paint: load it when the browser is idle. */
const loadAssistant = () => { const s = document.createElement("script"); s.src = "/assistant.js"; document.body.appendChild(s); };
("requestIdleCallback" in window) ? requestIdleCallback(loadAssistant, { timeout: 3000 }) : setTimeout(loadAssistant, 1500);
