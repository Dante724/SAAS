/* ════════════════════════════════════════════════════
   Icon system — clean inline SVG line icons.
   Usage: icon('wrench')  ·  icon('user', 18)
   Stroke inherits currentColor so they tint automatically.
════════════════════════════════════════════════════ */

const ICONS = {
  // navigation
  dashboard: '<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L4 16.8a1.8 1.8 0 0 0 2.5 2.5l5.3-5.3a4 4 0 0 0 5.2-5.4l-2.4 2.4-2.1-.4-.4-2.1z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M5 20a7 7 0 0 1 14 0"/>',
  users: '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19a5.5 5.5 0 0 1 11 0"/><path d="M16 5.2a3.2 3.2 0 0 1 0 6.1"/><path d="M17.5 13.5a5.5 5.5 0 0 1 3 5"/>',
  box: '<path d="M21 8v8a2 2 0 0 1-1 1.7l-7 4a2 2 0 0 1-2 0l-7-4A2 2 0 0 1 3 16V8a2 2 0 0 1 1-1.7l7-4a2 2 0 0 1 2 0l7 4A2 2 0 0 1 21 8z"/><path d="M3.3 7L12 12l8.7-5"/><path d="M12 22V12"/>',
  wallet: '<rect x="3" y="6" width="18" height="13" rx="2.5"/><path d="M3 10h18"/><circle cx="16.5" cy="14.5" r="1.3" fill="currentColor" stroke="none"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  'check-circle': '<circle cx="12" cy="12" r="9"/><path d="M8.5 12.5l2.5 2.5L16 9.5"/>',
  chart: '<path d="M4 20V10"/><path d="M10 20V4"/><path d="M16 20v-7"/><path d="M21 20H3"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  list: '<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><circle cx="3.5" cy="6" r="1" fill="currentColor" stroke="none"/><circle cx="3.5" cy="12" r="1" fill="currentColor" stroke="none"/><circle cx="3.5" cy="18" r="1" fill="currentColor" stroke="none"/>',
  hourglass: '<path d="M6 3h12"/><path d="M6 21h12"/><path d="M7 3c0 4 5 5 5 9s-5 5-5 9"/><path d="M17 3c0 4-5 5-5 9s5 5 5 9"/>',
  trending: '<path d="M3 17l6-6 4 4 7-7"/><path d="M17 8h4v4"/>',
  receipt: '<path d="M5 3v18l2-1.3L9 21l2-1.3L13 21l2-1.3L17 21l2-1.3V3l-2 1.3L15 3l-2 1.3L11 3 9 4.3 7 3z"/><path d="M8 8h8"/><path d="M8 12h8"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/>',
  logout: '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5"/><path d="M21 12H9"/>',
  // actions / status
  x: '<path d="M6 6l12 12"/><path d="M18 6L6 18"/>',
  plus: '<path d="M12 5v14"/><path d="M5 12h14"/>',
  alert: '<path d="M12 3l9.5 16.5H2.5z"/><path d="M12 10v4"/><circle cx="12" cy="17" r="0.6" fill="currentColor" stroke="none"/>',
  bolt: '<path d="M13 2L4 14h7l-1 8 9-12h-7l1-8z"/>',
  crown: '<path d="M3 8l4 3 5-6 5 6 4-3-2 11H5z"/>',
  camera: '<rect x="3" y="7" width="18" height="13" rx="2.5"/><path d="M8 7l1.5-2.5h5L16 7"/><circle cx="12" cy="13.5" r="3.2"/>',
  image: '<rect x="3" y="4" width="18" height="16" rx="2.5"/><circle cx="8.5" cy="9" r="1.6"/><path d="M4 17l4.5-4.5 3 3L15 11l5 5"/>',
  paperclip: '<path d="M20 11.5l-8.4 8.4a4.6 4.6 0 0 1-6.5-6.5l8.4-8.4a3 3 0 0 1 4.3 4.3l-8.1 8.1a1.5 1.5 0 0 1-2.2-2.1l7.4-7.4"/>',
  signature: '<path d="M3 17c2.5 0 3-9 5-9s1.5 6 3 6 1.5-4 3-4 1 3 2 3"/><path d="M3 21h18"/>',
  message: '<path d="M21 11.5a8 8 0 0 1-11.5 7.2L4 20l1.3-4.5A8 8 0 1 1 21 11.5z"/>',
  refresh: '<path d="M21 12a9 9 0 1 1-2.6-6.4"/><path d="M21 4v5h-5"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>',
  folder: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2.5h8a2 2 0 0 1 2 2V18a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  ban: '<circle cx="12" cy="12" r="9"/><path d="M5.6 5.6l12.8 12.8"/>',
  phone: '<path d="M5 4h3.5L10 8l-2 1.5a11 11 0 0 0 4.5 4.5L14 12l4 1.5V17a2 2 0 0 1-2 2A14 14 0 0 1 3 6a2 2 0 0 1 2-2z"/>',
  device: '<rect x="6" y="3" width="12" height="18" rx="2.5"/><path d="M10 18h4"/>',
  card: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3 9h18"/>',
  bank: '<path d="M4 10h16"/><path d="M4 10l8-6 8 6"/><path d="M6 10v8"/><path d="M10 10v8"/><path d="M14 10v8"/><path d="M18 10v8"/><path d="M3 21h18"/>',
  coin: '<ellipse cx="12" cy="7" rx="7" ry="3.2"/><path d="M5 7v6c0 1.8 3.1 3.2 7 3.2s7-1.4 7-3.2V7"/><path d="M5 13v3.5c0 1.8 3.1 3.2 7 3.2s7-1.4 7-3.2V13"/>',
  calendar: '<rect x="3" y="5" width="18" height="16" rx="2.5"/><path d="M3 9h18"/><path d="M8 3v4"/><path d="M16 3v4"/>',
  seedling: '<path d="M12 21v-8"/><path d="M12 13c0-3-2.5-5-6-5 0 3 2.5 5 6 5z"/><path d="M12 11c0-3 2.5-5 6-5 0 3-2.5 5-6 5z"/>',
  chevronLeft: '<path d="M15 6l-6 6 6 6"/>',
  chevronRight: '<path d="M9 6l6 6-6 6"/>',
  grid: '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="8" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/><rect x="13" y="13" width="8" height="8" rx="1.5"/>',
  cone: '<path d="M12 3L4 19h16z"/><path d="M7 13h10"/><path d="M9 8h6"/>',
  // Build v2 — sidebar parity with approved mockups
  mail: '<rect x="3" y="5" width="18" height="14" rx="2.5"/><path d="M3.5 7l8.5 6 8.5-6"/>',
  tag: '<path d="M3 3h7.6a2 2 0 0 1 1.4.6l8 8a2 2 0 0 1 0 2.8l-6.6 6.6a2 2 0 0 1-2.8 0l-8-8A2 2 0 0 1 2 11.6V4a1 1 0 0 1 1-1z"/><circle cx="7.5" cy="7.5" r="1.4" fill="currentColor" stroke="none"/>',
  settings: '<circle cx="12" cy="12" r="3.2"/><path d="M19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-1.8-.3 1.6 1.6 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.6 1.6 0 0 0-1-1.5 1.6 1.6 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0 .3-1.8 1.6 1.6 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.6 1.6 0 0 0 1.5-1 1.6 1.6 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.6 1.6 0 0 0 1 1.5 1.6 1.6 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V9a1.6 1.6 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>',
  tools: '<path d="M14.7 6.3a4 4 0 0 0-5.4 5.2L4 16.8a1.8 1.8 0 0 0 2.5 2.5l5.3-5.3a4 4 0 0 0 5.2-5.4l-2.4 2.4-2.1-.4-.4-2.1z"/>',
  banknote: '<rect x="2" y="6" width="20" height="12" rx="2.5"/><circle cx="12" cy="12" r="2.6"/><path d="M6 9v6M18 9v6"/>',
  bell: '<path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9"/><path d="M13.7 21a2 2 0 0 1-3.4 0"/>',
};

function icon(name, size = 16, strokeWidth = 1.8) {
  const path = ICONS[name];
  if (!path) return '';
  return `<svg class="ic" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" stroke-width="${strokeWidth}" stroke-linecap="round" stroke-linejoin="round"
    aria-hidden="true" style="display:inline-block;vertical-align:middle;flex-shrink:0">${path}</svg>`;
}
