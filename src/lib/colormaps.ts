/** Colormaps as control points (t in [0,1], RGB 0–1). The legend samples the same lookup
 *  table the viewer uses, so the two can never disagree. */
export type ColormapId = 'cool_to_warm' | 'rainbow' | 'viridis' | 'cividis' | 'reds' | 'advice' | 'noise' | 'grey';
export interface Colormap { id: ColormapId; label: string; points: [number, number, number, number][]; diverging?: boolean }

const hex = (value: string): [number, number, number] => {
  const n = parseInt(value.slice(1), 16);
  return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
};
const from = (stops: string[]): [number, number, number, number][] => stops.map((c, i) => [i / (stops.length - 1), ...hex(c)]);

export const COLORMAPS: Record<ColormapId, Colormap> = {
  cool_to_warm: { id: 'cool_to_warm', label: 'Cool to warm', diverging: true, points: [[0, 0.231, 0.298, 0.753], [0.25, 0.552, 0.69, 0.996], [0.5, 0.865, 0.865, 0.865], [0.75, 0.957, 0.604, 0.482], [1, 0.706, 0.016, 0.15]] },
  rainbow: { id: 'rainbow', label: 'Rainbow', points: from(['#1f3cbf', '#1e88e5', '#22c3d7', '#3fbf5f', '#d6d322', '#f59524', '#d7301f']) },
  viridis: { id: 'viridis', label: 'Viridis', points: from(['#440154', '#414487', '#2a788e', '#22a884', '#7ad151', '#fde725']) },
  cividis: { id: 'cividis', label: 'Cividis (colour-blind safe)', points: from(['#00224e', '#35456c', '#666970', '#948e77', '#c8b866', '#fee838']) },
  reds: { id: 'reds', label: 'Reds', points: from(['#fff5f0', '#fcbba1', '#fb6a4a', '#cb181d', '#67000d']) },
  advice: { id: 'advice', label: 'Design advice (inward / outward)', diverging: true, points: from(['#4053c5', '#d7d5f7', '#f6d4e6', '#ba1b2f']) },
  noise: { id: 'noise', label: 'Noise (magenta to red)', points: from(['#f5c2ff', '#e040fb', '#d81b60', '#b71c1c']) },
  grey: { id: 'grey', label: 'Grey', points: from(['#4b5563', '#e5e7eb']) },
};

export function sample(map: Colormap, t: number): [number, number, number] {
  const x = Math.min(1, Math.max(0, t));
  const points = map.points;
  for (let i = 1; i < points.length; i++) {
    if (x <= points[i][0]) {
      const [t0, r0, g0, b0] = points[i - 1];
      const [t1, r1, g1, b1] = points[i];
      const f = t1 > t0 ? (x - t0) / (t1 - t0) : 0;
      if (f >= 1) return [r1, g1, b1];
      return [r0 + f * (r1 - r0), g0 + f * (g1 - g0), b0 + f * (b1 - b0)];
    }
  }
  const last = points[points.length - 1];
  return [last[1], last[2], last[3]];
}

export function cssGradient(map: Colormap, direction = 'to right', steps = 32) {
  const stops = Array.from({ length: steps + 1 }, (_, i) => {
    const [r, g, b] = sample(map, i / steps);
    return `rgb(${Math.round(r * 255)} ${Math.round(g * 255)} ${Math.round(b * 255)}) ${((i / steps) * 100).toFixed(1)}%`;
  });
  return `linear-gradient(${direction}, ${stops.join(', ')})`;
}

/** Nice tick values (1-2-5 steps) covering [min, max]. */
export function niceTicks(min: number, max: number, target = 5): number[] {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [];
  if (min === max) return [min];
  const [low, high] = min < max ? [min, max] : [max, min];
  const raw = (high - low) / Math.max(1, target);
  const power = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((s) => s >= raw) ?? 10 * power;
  const first = Math.ceil(low / step - 1e-9) * step;
  const ticks: number[] = [];
  for (let v = first; v <= high + step * 1e-9; v += step) ticks.push(Math.abs(v) < step * 1e-9 ? 0 : Number(v.toPrecision(12)));
  return ticks;
}
