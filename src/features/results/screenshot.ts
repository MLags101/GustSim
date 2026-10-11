import type { StatusBlock } from '../../api/types';
import { COLORMAPS, niceTicks, sample, type ColormapId } from '../../lib/colormaps';
import { evidence } from '../../lib/evidence';
import { significant } from '../../lib/units';

/** Burn a title, the colour scale and the evidence states into a screenshot, so an image
 *  shared on its own still says what it shows and that it is not validated. */
export async function annotateScreenshot(image: string, info: { title: string; subtitle: string; colormap: ColormapId; range: [number, number]; unit?: string; status?: Partial<StatusBlock> }) {
  const img = new Image();
  await new Promise<void>((resolve, reject) => { img.onload = () => resolve(); img.onerror = () => reject(new Error('Could not read the screenshot')); img.src = image; });
  const footer = 96;
  const canvas = document.createElement('canvas');
  canvas.width = img.width; canvas.height = img.height + footer;
  const ctx = canvas.getContext('2d')!;
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(img, 0, 0);
  const y = img.height;
  ctx.fillStyle = '#0f172a'; ctx.font = '600 22px "Figtree Variable", sans-serif';
  ctx.fillText(info.title, 24, y + 34);
  ctx.fillStyle = '#475569'; ctx.font = '16px "Figtree Variable", sans-serif';
  ctx.fillText(info.subtitle + (info.unit && info.unit !== '1' ? ` [${info.unit}]` : ''), 24, y + 58);
  const states = evidence(info.status).map((e) => `${e.label}: ${e.value}`).join(' · ');
  ctx.font = '13px "Figtree Variable", sans-serif'; ctx.fillStyle = '#64748b';
  ctx.fillText(`GustSim · ${states}`, 24, y + 82);
  const barWidth = Math.min(360, canvas.width * 0.35), x0 = canvas.width - barWidth - 28, barY = y + 26;
  const map = COLORMAPS[info.colormap];
  for (let i = 0; i < barWidth; i++) {
    const [r, g, b] = sample(map, i / barWidth);
    ctx.fillStyle = `rgb(${r * 255},${g * 255},${b * 255})`;
    ctx.fillRect(x0 + i, barY, 1, 14);
  }
  ctx.fillStyle = '#334155'; ctx.font = '12px "Figtree Variable", sans-serif'; ctx.textAlign = 'center';
  const [low, high] = info.range;
  for (const t of niceTicks(low, high, 5)) {
    if (t < Math.min(low, high) || t > Math.max(low, high)) continue;
    ctx.fillText(significant(t, 3), x0 + ((t - low) / (high - low || 1)) * barWidth, barY + 32);
  }
  return canvas.toDataURL('image/png');
}
