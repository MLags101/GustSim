import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import { PRESETS, SI, type Quantity, type UnitSystem } from '../lib/units';

export type Theme = 'light' | 'dark' | 'system';

interface Preferences {
  theme: Theme;
  units: UnitSystem;
  tourCompleted: boolean;
  viewMode: 'table' | 'cards';
  setTheme: (theme: Theme) => void;
  setUnit: (quantity: Quantity, unit: string) => void;
  setUnitPreset: (preset: keyof typeof PRESETS) => void;
  setTourCompleted: (done: boolean) => void;
  setViewMode: (mode: 'table' | 'cards') => void;
}

const safeStorage = createJSONStorage(() => {
  try {
    const probe = '__gustsim_probe__';
    localStorage.setItem(probe, '1');
    localStorage.removeItem(probe);
    return localStorage;
  } catch {
    const memory = new Map<string, string>();
    return { getItem: (k: string) => memory.get(k) ?? null, setItem: (k: string, v: string) => void memory.set(k, v), removeItem: (k: string) => void memory.delete(k) };
  }
});

export const usePreferences = create<Preferences>()(persist((set) => ({
  theme: 'system',
  units: SI,
  tourCompleted: false,
  viewMode: 'table',
  setTheme: (theme) => set({ theme }),
  setUnit: (quantity, unit) => set((state) => ({ units: { ...state.units, [quantity]: unit } })),
  setUnitPreset: (preset) => set({ units: PRESETS[preset] }),
  setTourCompleted: (tourCompleted) => set({ tourCompleted }),
  setViewMode: (viewMode) => set({ viewMode }),
}), { name: 'gustsim.prefs.v2', storage: safeStorage, version: 1 }));

export function resolvedTheme(theme: Theme): 'light' | 'dark' {
  if (theme !== 'system') return theme;
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}
