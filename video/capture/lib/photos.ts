// Demo photos for the capture: a cracked iPhone on the bench mat (drawn on a canvas over the
// real bench-mat photo) and a crop of the battery from the same photo. Written as JPEGs.
import type { Browser } from "playwright";
import fs from "fs";
import path from "path";

const CRACKED_HTML = (bg: string) => `<!doctype html><html><body style="margin:0;background:#000">
<canvas id="c" width="1200" height="900"></canvas>
<script>
const c = document.getElementById('c'), x = c.getContext('2d');
const img = new Image();
img.onload = () => {
  // the blue bench mat, softly out of focus, as the surface
  x.filter = 'blur(6px) saturate(0.9) brightness(0.92)';
  x.drawImage(img, -120, -90, 1440, 1080);
  x.filter = 'none';
  // phone, slightly rotated, lying on the mat
  x.save();
  x.translate(600, 455); x.rotate(-0.12);
  const W = 400, H = 820, R = 62;
  const rr = (w, h, r) => { x.beginPath(); x.moveTo(-w/2+r,-h/2); x.arcTo(w/2,-h/2,w/2,h/2,r); x.arcTo(w/2,h/2,-w/2,h/2,r); x.arcTo(-w/2,h/2,-w/2,-h/2,r); x.arcTo(-w/2,-h/2,w/2,-h/2,r); x.closePath(); };
  // shadow + frame
  x.shadowColor = 'rgba(0,0,0,0.45)'; x.shadowBlur = 40; x.shadowOffsetY = 18;
  rr(W+22, H+22, R+8); x.fillStyle = '#2b2f36'; x.fill();
  x.shadowColor = 'transparent';
  rr(W+22, H+22, R+8); const fg = x.createLinearGradient(-W/2, 0, W/2, 0); fg.addColorStop(0,'#5b6270'); fg.addColorStop(.5,'#2d323a'); fg.addColorStop(1,'#646b78'); x.strokeStyle = fg; x.lineWidth = 5; x.stroke();
  // glass
  rr(W, H, R); x.save(); x.clip();
  const g = x.createLinearGradient(-W/2, -H/2, W/2, H/2); g.addColorStop(0,'#1a1d24'); g.addColorStop(.55,'#07080b'); g.addColorStop(1,'#14171d');
  x.fillStyle = g; x.fillRect(-W/2, -H/2, W, H);
  // soft reflection
  const rg = x.createLinearGradient(-W/2, -H/2, W/3, H/6); rg.addColorStop(0,'rgba(255,255,255,0.10)'); rg.addColorStop(.5,'rgba(255,255,255,0.03)'); rg.addColorStop(1,'rgba(255,255,255,0)');
  x.fillStyle = rg; x.beginPath(); x.moveTo(-W/2,-H/2); x.lineTo(W/2,-H/2); x.lineTo(-W/2, H/5); x.closePath(); x.fill();
  // notch
  x.fillStyle = '#000'; x.beginPath(); x.roundRect(-75, -H/2 - 4, 150, 34, 16); x.fill();
  // crack: impact point lower-right, radiating fractures with branches
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const ox = 70, oy = 190;
  const crack = (sx, sy, ang, len, w, depth) => {
    let px = sx, py = sy; x.beginPath(); x.moveTo(px, py);
    const steps = 6 + Math.floor(rnd()*6);
    for (let i = 0; i < steps; i++) {
      ang += (rnd() - 0.5) * 0.55; const d = len / steps;
      px += Math.cos(ang) * d; py += Math.sin(ang) * d; x.lineTo(px, py);
      if (depth < 2 && rnd() < 0.28) { x.stroke(); crack(px, py, ang + (rnd() < .5 ? -1 : 1) * (0.5 + rnd()*0.6), len * 0.45, w * 0.7, depth + 1); x.beginPath(); x.moveTo(px, py); }
    }
    x.lineWidth = w; x.stroke();
  };
  x.lineCap = 'round'; x.lineJoin = 'round';
  for (let pass = 0; pass < 2; pass++) {
    seed = 7;
    x.strokeStyle = pass ? 'rgba(235,240,248,0.85)' : 'rgba(0,0,0,0.6)';
    x.save(); if (!pass) x.translate(1.5, 1.5);
    for (let i = 0; i < 16; i++) crack(ox, oy, (i / 16) * Math.PI * 2 + rnd()*0.3, 180 + rnd() * 420, pass ? 1.6 : 2.4, 0);
    x.restore();
  }
  // shattered centre: small shards
  seed = 99;
  for (let i = 0; i < 70; i++) { const a = rnd()*Math.PI*2, d = rnd()*48; x.strokeStyle = 'rgba(230,236,245,' + (0.35 + rnd()*0.5) + ')'; x.lineWidth = 1 + rnd(); x.beginPath(); x.moveTo(ox + Math.cos(a)*d, oy + Math.sin(a)*d); x.lineTo(ox + Math.cos(a+0.7)*(d+14), oy + Math.sin(a+0.7)*(d+14)); x.stroke(); }
  const hg = x.createRadialGradient(ox, oy, 0, ox, oy, 60); hg.addColorStop(0,'rgba(255,255,255,0.35)'); hg.addColorStop(1,'rgba(255,255,255,0)'); x.fillStyle = hg; x.beginPath(); x.arc(ox, oy, 60, 0, Math.PI*2); x.fill();
  x.restore();
  x.restore();
  document.title = 'ready';
};
img.src = '${bg}';
</script></body></html>`;

export async function makePhotos(browser: Browser, outDir: string, matPhoto: string) {
  fs.mkdirSync(outDir, { recursive: true });
  const bg = "data:image/webp;base64," + fs.readFileSync(matPhoto).toString("base64");
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 } });
  await page.setContent(CRACKED_HTML(bg));
  await page.waitForFunction(() => document.title === "ready");
  const cracked = path.join(outDir, "iphone13-cracked.jpg");
  await page.locator("#c").screenshot({ path: cracked, type: "jpeg", quality: 88 });
  // the battery in the real bench photo (left of the mat): a crop for the recommendation
  await page.setContent(`<body style="margin:0"><img id="i" src="${bg}" style="display:block"></body>`);
  await page.setViewportSize({ width: 960, height: 640 });
  const battery = path.join(outDir, "iphone13-battery.jpg");
  await page.screenshot({ path: battery, type: "jpeg", quality: 85, clip: { x: 92, y: 236, width: 160, height: 280 } });
  await page.close();
  return { cracked, battery };
}
