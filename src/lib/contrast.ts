/** WCAG 2 contrast. Used to keep the theme tokens readable; not a full page audit. */

function channel(value: number) {
  const c = value / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

export function luminance(hex: string) {
  const h = hex.replace('#', '');
  const r = channel(parseInt(h.slice(0, 2), 16));
  const g = channel(parseInt(h.slice(2, 4), 16));
  const b = channel(parseInt(h.slice(4, 6), 16));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(foreground: string, background: string) {
  const lighter = Math.max(luminance(foreground), luminance(background));
  const darker = Math.min(luminance(foreground), luminance(background));
  return (lighter + 0.05) / (darker + 0.05);
}

/** Pairs that carry body or button text. Each must clear WCAG AA for normal text (4.5:1). */
export const TEXT_PAIRS: [string, string, string][] = [
  ['Ink on page', '#0f172a', '#f4f7fa'],
  ['Secondary ink on white', '#334155', '#ffffff'],
  ['Muted on white', '#64748b', '#ffffff'],
  ['Primary button', '#ffffff', '#0369a1'],
  ['Accent ink on soft', '#0369a1', '#e0f2fe'],
  ['Dark ink on page', '#e2e8f0', '#0b1120'],
  ['Dark secondary on surface', '#cbd5e1', '#0f172a'],
  ['Dark muted on surface', '#94a3b8', '#0f172a'],
  ['Dark primary button', '#0f172a', '#38bdf8'],
];
