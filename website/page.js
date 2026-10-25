/* Shared behaviour for the industry landing pages (the homepage has its own inline script). */
(() => {
  const C = window.TEKLY_SITE || {};
  const $ = (s, el = document) => el.querySelector(s), $$ = (s, el = document) => [...el.querySelectorAll(s)];
  const appUrl = (C.appUrl || "").replace(/\/$/, "");
  $$("[data-app]").forEach(a => { a.href = appUrl + (a.dataset.app === "signup" ? C.signUpPath || "/signup" : C.signInPath || "/login"); });
  $$("[data-mail]").forEach(a => { a.href = "mailto:" + (C.demoEmail || ""); });
  $$("[data-trial]").forEach(el => { el.textContent = C.trialDays ?? 14; });
  const nav = $("#nav"), burger = $(".burger");
  addEventListener("scroll", () => nav.classList.toggle("stuck", scrollY > 4), { passive: true });
  burger.addEventListener("click", () => { const o = nav.classList.toggle("open"); burger.setAttribute("aria-expanded", String(o)); });
  addEventListener("keydown", e => { if (e.key === "Escape" && nav.classList.contains("open")) { nav.classList.remove("open"); burger.setAttribute("aria-expanded", "false"); burger.focus(); } });
  const io = new IntersectionObserver(es => es.forEach(x => { if (x.isIntersecting) { x.target.classList.add("shown"); io.unobserve(x.target); } }), { rootMargin: "0px 0px -8% 0px" });
  $$(".rv").forEach(el => io.observe(el));
  document.dispatchEvent(new Event("tekly:links-ready"));
})();
