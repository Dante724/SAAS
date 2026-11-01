/* Colour helpers for values that only exist at runtime (a status's own hex, an avatar hue).
   Both read CSS variables set per theme in index.css, so the same call looks right in light
   and dark: tints get stronger on dark (at 12% every hue collapses into the same navy), and
   coloured text is lifted toward white so it stays readable on dark surfaces. */

/** A soft fill of `hex` at `pct`% in light mode (about 1.8× that in dark). */
export const tint = (hex: string, pct = 12) => `color-mix(in srgb, ${hex} calc(${pct}% * var(--tint-boost, 1)), transparent)`;

/** `hex` as text: unchanged in light, mixed toward white in dark. */
export const hueText = (hex: string) => `color-mix(in srgb, ${hex}, white var(--hue-lift, 0%))`;
