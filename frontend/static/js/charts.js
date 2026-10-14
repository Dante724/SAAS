/* Build 144ad — lightweight inline-SVG charts + number motion.

   Zero dependencies: every chart is an SVG string coloured from the design
   tokens (var(--blue) …), so it inherits light/dark automatically. Used by the
   Reports page (table ↔ chart) and available anywhere. */
(function () {
  const PALETTE = ['var(--blue)', 'var(--purple)', 'var(--green)', 'var(--amber)', 'var(--teal)', 'var(--pink)', 'var(--red)'];
  const esc = (s) => String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const nice = (n) => {
    const a = Math.abs(n);
    if (a >= 1e7) return (n / 1e7).toFixed(1) + 'Cr';
    if (a >= 1e5) return (n / 1e5).toFixed(1) + 'L';
    if (a >= 1e3) return (n / 1e3).toFixed(1) + 'k';
    return Math.round(n * 100) / 100;
  };

  const Charts = {
    // Vertical bars. rows: [[label, value], …]
    bars(pairs, { height = 260, unit = '' } = {}) {
      const data = pairs.filter(p => typeof p[1] === 'number');
      if (!data.length) return '<div class="chart-empty">Nothing to chart</div>';
      const max = Math.max(...data.map(d => d[1]), 0) || 1;
      const min = Math.min(...data.map(d => d[1]), 0);
      const W = Math.max(data.length * 64 + 40, 320), H = height, pad = 30, base = H - 34;
      const span = max - min || 1;
      const y = (v) => base - ((v - min) / span) * (base - pad);
      const bw = Math.min(46, (W - 40) / data.length - 14);
      const bars = data.map((d, i) => {
        const x = 30 + i * ((W - 40) / data.length) + ((W - 40) / data.length - bw) / 2;
        const yv = y(d[1]), y0 = y(Math.max(0, min));
        const top = Math.min(yv, y0), h = Math.max(2, Math.abs(yv - y0));
        return `<rect x="${x.toFixed(1)}" y="${top.toFixed(1)}" width="${bw.toFixed(1)}" height="${h.toFixed(1)}" rx="6"
                  fill="url(#barg)"><title>${esc(d[0])}: ${esc(d[1])}</title></rect>
                <text x="${(x + bw / 2).toFixed(1)}" y="${(top - 6).toFixed(1)}" text-anchor="middle" class="c-val">${nice(d[1])}</text>
                <text x="${(x + bw / 2).toFixed(1)}" y="${H - 12}" text-anchor="middle" class="c-lab">${esc(String(d[0]).slice(0, 10))}</text>`;
      }).join('');
      return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img">
        <defs><linearGradient id="barg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="var(--blue)"/><stop offset="1" stop-color="var(--purple)"/></linearGradient></defs>
        <line x1="24" y1="${base}" x2="${W - 12}" y2="${base}" class="c-axis"/>${bars}
        ${unit ? `<text x="24" y="16" class="c-lab">${esc(unit)}</text>` : ''}</svg>`;
    },

    // Trend line with soft area fill. pairs: [[label, value], …]
    line(pairs, { height = 260 } = {}) {
      const data = pairs.filter(p => typeof p[1] === 'number');
      if (data.length < 2) return this.bars(pairs, { height });
      const max = Math.max(...data.map(d => d[1])), min = Math.min(...data.map(d => d[1]));
      const W = Math.max(data.length * 60 + 40, 360), H = height, pad = 24, base = H - 34;
      const span = (max - min) || 1;
      const X = (i) => 30 + i * ((W - 50) / (data.length - 1));
      const Y = (v) => base - ((v - min) / span) * (base - pad);
      const pts = data.map((d, i) => `${X(i).toFixed(1)},${Y(d[1]).toFixed(1)}`).join(' ');
      const area = `30,${base} ${pts} ${X(data.length - 1).toFixed(1)},${base}`;
      const dots = data.map((d, i) => `<circle cx="${X(i).toFixed(1)}" cy="${Y(d[1]).toFixed(1)}" r="3.5" fill="var(--blue)"><title>${esc(d[0])}: ${esc(d[1])}</title></circle>
        <text x="${X(i).toFixed(1)}" y="${H - 12}" text-anchor="middle" class="c-lab">${esc(String(d[0]).slice(0, 10))}</text>`).join('');
      return `<svg class="chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet" role="img">
        <defs><linearGradient id="areg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" stop-color="var(--blue)" stop-opacity="0.28"/><stop offset="1" stop-color="var(--blue)" stop-opacity="0"/></linearGradient></defs>
        <line x1="24" y1="${base}" x2="${W - 12}" y2="${base}" class="c-axis"/>
        <polygon points="${area}" fill="url(#areg)"/>
        <polyline points="${pts}" fill="none" stroke="var(--blue)" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>
        ${dots}</svg>`;
    },

    // Tiny inline trend, no axes. Returns a small SVG for stat cards.
    sparkline(values, { w = 120, h = 34, color = 'var(--blue)' } = {}) {
      const v = (values || []).filter(x => typeof x === 'number');
      if (v.length < 2) return '';
      const max = Math.max(...v), min = Math.min(...v), span = (max - min) || 1;
      const X = (i) => (i / (v.length - 1)) * (w - 4) + 2;
      const Y = (n) => h - 3 - ((n - min) / span) * (h - 6);
      const pts = v.map((n, i) => `${X(i).toFixed(1)},${Y(n).toFixed(1)}`).join(' ');
      return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polyline points="${pts}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        <circle cx="${X(v.length - 1).toFixed(1)}" cy="${Y(v[v.length - 1]).toFixed(1)}" r="2.4" fill="${color}"/></svg>`;
    },
  };

  // Animate a number up to its value, preserving any currency prefix/suffix.
  // Respects prefers-reduced-motion (sets the final value instantly).
  function countUp(el) {
    if (!el) return;
    const finalText = el.textContent.trim();
    const m = finalText.match(/-?[\d,]*\.?\d+/);
    if (!m) return;
    const target = parseFloat(m[0].replace(/,/g, ''));
    if (!isFinite(target)) return;
    const pre = finalText.slice(0, m.index), post = finalText.slice(m.index + m[0].length);
    const decimals = (m[0].split('.')[1] || '').length;
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    if (reduce || target === 0) { el.textContent = finalText; return; }
    const dur = 620, t0 = performance.now();
    const fmtN = (n) => n.toLocaleString('en-IN', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
    function step(t) {
      const p = Math.min(1, (t - t0) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = pre + fmtN(target * eased) + post;
      if (p < 1) requestAnimationFrame(step); else el.textContent = finalText;
    }
    requestAnimationFrame(step);
  }
  function countUpAll(root) {
    if (!root) return;
    root.querySelectorAll('[data-countup], .dt-val').forEach(countUp);
  }

  window.Charts = Charts;
  window.countUp = countUp;
  window.countUpAll = countUpAll;
})();
