// One recorded clip: a fresh browser context with Playwright video, a visible fake cursor
// (desktop) or touch dots (mobile), smooth input helpers, beat markers, and post-processing
// (trim the dead load time, re-encode, poster PNG, duration probe).
import type { Browser, BrowserContext, CDPSession, Locator, Page } from "playwright";
import { execFileSync } from "child_process";
import fs from "fs";
import path from "path";

const COMPOSITOR = path.resolve(__dirname, "../../node_modules/@remotion/compositor-darwin-arm64");
export const FFMPEG = path.join(COMPOSITOR, "ffmpeg");
export const FFPROBE = path.join(COMPOSITOR, "ffprobe");
for (const b of [FFMPEG, FFPROBE]) { try { fs.chmodSync(b, 0o755); } catch { /* ignore */ } }
const XO = { cwd: COMPOSITOR, env: { ...process.env, DYLD_LIBRARY_PATH: COMPOSITOR } };

export const FPS = 25;
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

// Injected into every page: a macOS-style arrow that follows mouse events, a click pulse,
// touch dots on phones, and the bits of chrome we keep out of the promo.
const STAGE_SCRIPT = (mode: "cursor" | "touch") => `
(() => {
  window.__name = window.__name || ((f) => f);   // tsx helper referenced by evaluated functions
  const MODE = ${JSON.stringify(mode)};
  const css = \`
    #__cap_cursor{position:fixed;left:0;top:0;width:26px;height:26px;z-index:2147483647;pointer-events:none;
      transform:translate(-100px,-100px);transition:none;filter:drop-shadow(0 2px 3px rgba(0,0,0,.28))}
    #__cap_cursor.down svg{transform:scale(.88);transform-origin:3px 2px}
    .__cap_pulse{position:fixed;z-index:2147483646;pointer-events:none;width:36px;height:36px;margin:-18px 0 0 -18px;border-radius:50%;
      background:rgba(37,99,235,.22);border:2px solid rgba(37,99,235,.55);animation:__cap_p .55s ease-out forwards}
    .__cap_touch{position:fixed;z-index:2147483646;pointer-events:none;width:44px;height:44px;margin:-22px 0 0 -22px;border-radius:50%;
      background:rgba(15,23,42,.18);border:2px solid rgba(255,255,255,.85);box-shadow:0 1px 6px rgba(0,0,0,.25);transition:opacity .35s}
    @keyframes __cap_p{from{transform:scale(.4);opacity:1}to{transform:scale(1.5);opacity:0}}
    /* chrome kept out of the promo: trial badge, smart-suggestion widgets, dev toasts */
    [data-cap-hide]{display:none!important}
    html{scroll-behavior:auto}
  \`;
  // [text pattern, how many levels up to hide]
  const HIDE = [[/^Trial$/, 0], [/^Draft from the complaint$/, 0], [/^Learning your shop —/, 0], [/^Usually added$/, 1], [/^Tekly Sense/, 1], [/^Morning Brief/, 1]];
  function hideBits(root){
    for (const el of root.querySelectorAll('span,div,a,button,p,h2,h3,h4')) {
      if (el.hasAttribute('data-cap-hide')) continue;
      const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('').trim();
      if (!own) continue;
      if (own === 'Job summary') { let t = el; while (t.parentElement && !/Notes & files/.test(t.parentElement.textContent || '')) t = t.parentElement; t.setAttribute('data-cap-hide',''); continue; }
      for (const [re, up] of HIDE) if (re.test(own)) { let t = el; for (let i = 0; i < up && t.parentElement; i++) t = t.parentElement; if (t.tagName !== 'BUTTON' && el.closest('button')) t = el.closest('button'); t.setAttribute('data-cap-hide',''); }
    }
  }
  function boot(){
    if (document.getElementById('__cap_style')) return;
    const st = document.createElement('style'); st.id='__cap_style'; st.textContent = css; document.head.appendChild(st);
    hideBits(document.body);
    new MutationObserver(() => hideBits(document.body)).observe(document.body, {childList:true, subtree:true});
    if (MODE === 'cursor') {
      const c = document.createElement('div'); c.id='__cap_cursor';
      c.innerHTML = '<svg width="26" height="26" viewBox="0 0 26 26"><path d="M3 2 L3 21 L8 16.5 L11.4 24 L14.6 22.6 L11.3 15.3 L18 15.3 Z" fill="#0b0f19" stroke="#fff" stroke-width="1.6" stroke-linejoin="round"/></svg>';
      document.body.appendChild(c);
      const last = window.__capPos; if (last) c.style.transform = 'translate(' + last[0] + 'px,' + last[1] + 'px)';
      addEventListener('mousemove', (e) => { window.__capPos=[e.clientX,e.clientY]; c.style.transform = 'translate(' + e.clientX + 'px,' + e.clientY + 'px)'; }, true);
      addEventListener('mousedown', (e) => { c.classList.add('down'); const p=document.createElement('div'); p.className='__cap_pulse'; p.style.left=e.clientX+'px'; p.style.top=e.clientY+'px'; document.body.appendChild(p); setTimeout(()=>p.remove(),600); }, true);
      addEventListener('mouseup', () => c.classList.remove('down'), true);
    } else {
      const dots = new Map();
      const place = (t) => { let d = dots.get(t.identifier); if (!d) { d = document.createElement('div'); d.className='__cap_touch'; document.body.appendChild(d); dots.set(t.identifier, d); } d.style.left=t.clientX+'px'; d.style.top=t.clientY+'px'; d.style.opacity='1'; };
      addEventListener('touchstart', (e) => { for (const t of e.changedTouches) place(t); }, {capture:true, passive:true});
      addEventListener('touchmove', (e) => { for (const t of e.changedTouches) place(t); }, {capture:true, passive:true});
      const end = (e) => { for (const t of e.changedTouches) { const d = dots.get(t.identifier); if (d) { dots.delete(t.identifier); d.style.opacity='0'; setTimeout(()=>d.remove(), 400); } } };
      addEventListener('touchend', end, {capture:true, passive:true}); addEventListener('touchcancel', end, {capture:true, passive:true});
    }
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();`;

export type Beat = { t: number; label: string };
export type ClipMeta = { file: string; width: number; height: number; durationSec: number; fps: number; beats: Beat[]; poster: string; note?: string };

export class Clip {
  page!: Page; ctx!: BrowserContext; cdp?: CDPSession;
  private t0 = 0; private startMs = 0; private beats: Beat[] = []; private posterAt: number | null = null;
  private pos = { x: 720, y: 450 };
  constructor(public name: string, public mobile: boolean, private outDir: string, private rawDir: string) {}

  get width() { return this.mobile ? 780 : 1440; }
  get height() { return this.mobile ? 1688 : 900; }

  static async open(browser: Browser, name: string, opts: { mobile?: boolean; token?: string; outDir: string; rawDir: string }) {
    const c = new Clip(name, !!opts.mobile, opts.outDir, opts.rawDir);
    const dir = path.join(opts.rawDir, name); fs.rmSync(dir, { recursive: true, force: true }); fs.mkdirSync(dir, { recursive: true });
    c.ctx = await browser.newContext(c.mobile
      // A 390×844 phone rendered at 2×: Playwright's recorder captures CSS pixels, so the window is
      // 780×1688 and the page's meta viewport is pinned to width=390 at scale 2 (sharp text, same layout).
      ? { viewport: { width: 780, height: 1688 }, deviceScaleFactor: 1, isMobile: true, hasTouch: true, locale: "en-US", timezoneId: "America/Los_Angeles",
          userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
          recordVideo: { dir, size: { width: 780, height: 1688 } }, colorScheme: "light" }
      : { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: "en-US", timezoneId: "America/Los_Angeles",
          recordVideo: { dir, size: { width: 1440, height: 900 } }, colorScheme: "light" });
    if (opts.token) await c.ctx.addInitScript((t) => { try { localStorage.setItem("ros_tok", t); } catch { /* */ } }, opts.token);
    await c.ctx.addInitScript(STAGE_SCRIPT(c.mobile ? "touch" : "cursor"));
    if (c.mobile) await c.ctx.addInitScript(() => {
      const VP = "width=390, initial-scale=2, minimum-scale=2, maximum-scale=2, user-scalable=no";
      const fix = () => { const ms = document.querySelectorAll('meta[name="viewport"]'); if (ms.length) { ms.forEach((m) => { if ((m as HTMLMetaElement).content !== VP) (m as HTMLMetaElement).content = VP; }); return true; } return false; };
      const mo = new MutationObserver(() => fix()); mo.observe(document, { childList: true, subtree: true, attributes: true, attributeFilter: ["content"] });
      document.addEventListener("DOMContentLoaded", () => { if (!fix()) { const m = document.createElement("meta"); m.name = "viewport"; m.content = VP; document.head.appendChild(m); } });
    });
    c.page = await c.ctx.newPage();
    c.t0 = Date.now();
    if (c.mobile) c.cdp = await c.ctx.newCDPSession(c.page);
    return c;
  }

  /** Everything before this moment (page loads) is trimmed off the clip. */
  async markStart() {
    if (!this.mobile) await this.page.mouse.move(this.pos.x, this.pos.y);
    await sleep(150);
    this.startMs = Date.now() - this.t0;
  }
  now() { return Math.max(0, (Date.now() - this.t0 - this.startMs) / 1000); }
  beat(label: string, poster = false) {
    const t = +this.now().toFixed(2); this.beats.push({ t, label });
    if (poster) this.posterAt = t;
    console.log(`  [${this.name}] ${t.toFixed(2)}s  ${label}`);
  }
  async pause(ms = 900) { await sleep(ms); }

  // ── desktop pointer ──
  async moveTo(x: number, y: number, ms?: number) {
    const from = { ...this.pos };
    const dist = Math.hypot(x - from.x, y - from.y);
    const dur = ms ?? Math.min(1100, 320 + dist * 0.75);
    const n = Math.max(2, Math.round(dur / 16));
    for (let i = 1; i <= n; i++) {
      const k = i / n; const e = k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2;
      // a slight arc so it doesn't look robotic
      const arc = Math.sin(Math.PI * k) * Math.min(40, dist * 0.06);
      const px = from.x + (x - from.x) * e, py = from.y + (y - from.y) * e - arc * 0.4;
      await this.page.mouse.move(px, py);
      await sleep(dur / n);
    }
    this.pos = { x, y };
  }
  async box(l: Locator) {
    await l.waitFor({ state: "visible", timeout: 20000 });
    const b = await l.boundingBox(); if (!b) throw new Error("no box");
    return b;
  }
  async hover(l: Locator, dx = 0.5, dy = 0.5) {
    await l.scrollIntoViewIfNeeded();
    const b = await this.box(l);
    await this.moveTo(b.x + b.width * dx, b.y + b.height * dy);
  }
  async click(l: Locator, opts: { dx?: number; dy?: number; settle?: number } = {}) {
    await this.hover(l, opts.dx ?? 0.5, opts.dy ?? 0.5);
    await sleep(160);
    await this.page.mouse.down(); await sleep(90); await this.page.mouse.up();
    await sleep(opts.settle ?? 650);
  }
  async type(l: Locator, text: string, delay = 75) {
    await this.click(l, { settle: 250 });
    await l.pressSequentially(text, { delay });
    await sleep(350);
  }
  async drag(points: { x: number; y: number }[], stepMs = 16) {
    await this.moveTo(points[0].x, points[0].y);
    await this.page.mouse.down();
    for (const p of points.slice(1)) { await this.page.mouse.move(p.x, p.y); this.pos = p; await sleep(stepMs); }
    await this.page.mouse.up();
  }
  /** Smoothly scroll the element into view (its scroll container animates). */
  async scrollTo(l: Locator, block: "start" | "center" | "end" = "center", ms = 900) {
    await l.waitFor({ state: "attached", timeout: 20000 });
    await l.evaluate((el, [block, ms]) => new Promise<void>((done) => {
      let sc: HTMLElement | null = el.parentElement;
      while (sc && !(sc.scrollHeight > sc.clientHeight + 2 && /(auto|scroll)/.test(getComputedStyle(sc).overflowY))) sc = sc.parentElement;
      const target = sc || document.scrollingElement as HTMLElement;
      const er = el.getBoundingClientRect(); const cr = sc ? sc.getBoundingClientRect() : { top: 0, height: innerHeight } as DOMRect;
      let delta = er.top - cr.top - (block === "start" ? 24 : block === "end" ? cr.height - er.height - 24 : (cr.height - er.height) / 2);
      const start = target.scrollTop; const max = target.scrollHeight - target.clientHeight;
      const end = Math.max(0, Math.min(max, start + delta)); const t0 = performance.now();
      const step = (now: number) => { const k = Math.min(1, (now - t0) / (ms as number)); const e = k < .5 ? 4*k*k*k : 1 - Math.pow(-2*k+2,3)/2;
        target.scrollTop = start + (end - start) * e; if (k < 1) requestAnimationFrame(step); else done(); };
      requestAnimationFrame(step);
    }), [block, ms] as const);
    await sleep(200);
  }

  // ── mobile touch ──
  async tap(l: Locator, settle = 700) {
    await l.scrollIntoViewIfNeeded();
    const b = await this.box(l);
    const x = b.x + b.width / 2, y = b.y + b.height / 2;
    await this.cdp!.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y }] });
    await sleep(110);
    await this.cdp!.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await sleep(settle);
  }
  async swipe(fromY: number, toY: number, ms = 700, x = 195) {
    const n = Math.max(6, Math.round(ms / 16));
    await this.cdp!.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [{ x, y: fromY }] });
    for (let i = 1; i <= n; i++) {
      const k = i / n; const e = 1 - Math.pow(1 - k, 2);
      await this.cdp!.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ x, y: fromY + (toY - fromY) * e }] });
      await sleep(ms / n);
    }
    await this.cdp!.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
    await sleep(250);
  }
  /** Scroll the page smoothly (eased, like a flick settling) so `l` sits near `frac` of the screen height. */
  async scrollMobileTo(l: Locator, frac = 0.35, msPer1000 = 900) {
    await l.waitFor({ state: "attached" });
    const b = await l.boundingBox(); if (!b) return;
    const dy = Math.round(b.y - 844 * frac);
    if (Math.abs(dy) < 8) return;
    const ms = Math.max(450, Math.min(1400, Math.abs(dy) / 1000 * msPer1000 + 300));
    await this.page.evaluate(([dy, ms]) => new Promise<void>((done) => {
      const s0 = scrollY, t0 = performance.now();
      const step = (now: number) => { const k = Math.min(1, (now - t0) / ms); const e = 1 - Math.pow(1 - k, 3); scrollTo(0, s0 + dy * e); if (k < 1) requestAnimationFrame(step); else done(); };
      requestAnimationFrame(step); }), [dy, ms] as const);
    await sleep(250);
  }
  async touchPath(points: { x: number; y: number }[], stepMs = 14) {
    await this.cdp!.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [points[0]] });
    for (const p of points.slice(1)) { await this.cdp!.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [p] }); await sleep(stepMs); }
    await this.cdp!.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
  }

  async finish(note?: string): Promise<ClipMeta> {
    await sleep(500);
    const video = this.page.video()!;
    await this.ctx.close();
    const raw = await video.path();
    const out = path.join(this.outDir, `${this.name}.webm`);
    const ss = Math.max(0, this.startMs / 1000);
    execFileSync(FFMPEG, ["-y", "-loglevel", "error", "-ss", ss.toFixed(3), "-i", raw, "-an",
      "-c:v", "libvpx-vp9", "-crf", "18", "-b:v", "0", "-deadline", "good", "-cpu-used", "4", "-row-mt", "1",
      "-pix_fmt", "yuv420p", "-r", String(FPS), out], XO);
    const durationSec = probeDuration(out);
    const posterT = Math.min(durationSec - 0.1, this.posterAt ?? (this.beats.length ? this.beats[this.beats.length - 1].t : durationSec / 2));
    const poster = path.join(this.outDir, `${this.name}.png`);
    execFileSync(FFMPEG, ["-y", "-loglevel", "error", "-ss", Math.max(0, posterT).toFixed(2), "-i", out, "-frames:v", "1", poster], XO);
    return { file: `${this.name}.webm`, width: this.width, height: this.height, durationSec: +durationSec.toFixed(2), fps: FPS,
             beats: this.beats.filter((b) => b.t <= durationSec), poster: `${this.name}.png`, ...(note ? { note } : {}) };
  }
}

export function probeDuration(file: string): number {
  const s = execFileSync(FFPROBE, ["-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", file], XO).toString().trim();
  return parseFloat(s);
}

export function countFrames(file: string): number {
  const s = execFileSync(FFPROBE, ["-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries", "stream=nb_read_frames", "-of", "default=nw=1:nk=1", file], XO).toString().trim();
  return parseInt(s, 10);
}
