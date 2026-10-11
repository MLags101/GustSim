import { useEffect, useState } from 'react';
import type { CameraPreset, Vec3 } from '../api/types';

function rgb(value: string): [number, number, number] {
  const v = value.trim();
  if (v.startsWith('#')) {
    const n = parseInt(v.length === 4 ? v.slice(1).split('').map((c) => c + c).join('') : v.slice(1), 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  }
  const m = v.match(/\d+(\.\d+)?/g);
  return m ? [Number(m[0]) / 255, Number(m[1]) / 255, Number(m[2]) / 255] : [0.95, 0.96, 0.98];
}

export interface ViewerTheme { background: [number, number, number]; neutral: number[]; selected: number[]; highlight: number[]; dark: boolean }

function read(): ViewerTheme {
  const style = getComputedStyle(document.documentElement);
  const to255 = (c: [number, number, number]) => c.map((x) => Math.round(x * 255));
  return {
    background: rgb(style.getPropertyValue('--viewer-bottom') || '#f9fbfd'),
    neutral: to255(rgb(style.getPropertyValue('--model-neutral') || '#b8c4cf')),
    selected: to255(rgb(style.getPropertyValue('--model-selected') || '#02bbef')),
    highlight: to255(rgb(style.getPropertyValue('--model-rotor') || '#fc7253')),
    dark: document.documentElement.dataset.theme === 'dark',
  };
}

/** Viewer colours from the CSS tokens, updated when the theme attribute changes. */
export function useViewerTheme(): ViewerTheme {
  const [theme, setTheme] = useState(read);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(read()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  return theme;
}

const unit = (v: number[]) => { const n = Math.hypot(...v) || 1; return v.map((x) => x / n) as Vec3; };
const cross = (a: number[], b: number[]): Vec3 => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];

export type PresetName = 'iso' | 'front' | 'rear' | 'left' | 'right' | 'top' | 'bottom';
export const PRESET_LABELS: Record<PresetName, string> = { iso: '3D', front: 'Front', rear: 'Rear', left: 'Left', right: 'Right', top: 'Top', bottom: 'Bottom' };

/** Camera presets relative to the flow: "front" looks at the model from upstream. */
export function flowCamera(name: PresetName, bounds: number[][], flow: number[] = [1, 0, 0], lift: number[] = [0, 0, 1]): CameraPreset {
  const drag = unit(Math.hypot(...flow) > 1e-12 ? flow : [1, 0, 0]);
  let up = lift.map((v, i) => v - (lift[0] * drag[0] + lift[1] * drag[1] + lift[2] * drag[2]) * drag[i]);
  if (Math.hypot(...up) < 1e-6) up = Math.abs(drag[2]) < 0.9 ? [0, 0, 1] : [0, 1, 0];
  const u = unit(up);
  const side = cross(u, drag);
  const center = bounds[0].map((v, i) => (v + bounds[1][i]) / 2) as Vec3;
  const size = Math.hypot(...bounds[0].map((v, i) => bounds[1][i] - v)) || 1;
  const directions: Record<PresetName, [number[], number[]]> = {
    front: [drag.map((v) => -v), u], rear: [drag, u], left: [side, u], right: [side.map((v) => -v), u],
    top: [u, drag.map((v) => -v)], bottom: [u.map((v) => -v), drag.map((v) => -v)],
    iso: [drag.map((v, i) => -v + side[i] * 0.9 + u[i] * 0.7), u],
  };
  const [direction, viewUp] = directions[name];
  const d = unit(direction);
  return { focal_point: center, position: center.map((c, i) => c + d[i] * size * 2.2) as Vec3, view_up: unit(viewUp) };
}
