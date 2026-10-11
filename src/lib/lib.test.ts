import { describe, expect, it } from 'vitest';
import fixtures from '../test/fixtures/euler_sxyz.json';
import { COLORMAPS, cssGradient, niceTicks, sample } from './colormaps';
import { configurationKey } from './configurationKey';
import { rotation, transformMatrix, apply, transformBounds } from './euler';
import { contrast, TEXT_PAIRS } from './contrast';
import { evidence, validationLabel } from './evidence';
import { UNITS, formatQuantity, fromDisplay, toDisplay, type Quantity } from './units';

describe('units', () => {
  it('round-trips every unit without drift', () => {
    for (const quantity of Object.keys(UNITS) as Quantity[]) {
      for (const unit of Object.keys(UNITS[quantity])) {
        for (const value of [0, 1, -3.25, 1234.5678, 1e-6]) {
          const back = fromDisplay(quantity, toDisplay(quantity, value, unit), unit);
          expect(Math.abs(back - value)).toBeLessThanOrEqual(Math.abs(value) * 1e-12 + 1e-18);
        }
      }
    }
  });
  it('converts familiar values', () => {
    expect(toDisplay('speed', 10, 'km/h')).toBeCloseTo(36);
    expect(toDisplay('force', 9.80665, 'kgf')).toBeCloseTo(1);
    expect(formatQuantity('power', 1500, 'kW')).toBe('1.5 kW');
    expect(formatQuantity('force', null, 'N')).toBe('—');
  });
});

describe('colormaps and ticks', () => {
  it('samples the endpoints of every map', () => {
    for (const map of Object.values(COLORMAPS)) {
      const [, r0, g0, b0] = map.points[0];
      const [, r1, g1, b1] = map.points[map.points.length - 1];
      expect(sample(map, 0)).toEqual([r0, g0, b0]);
      expect(sample(map, 1)).toEqual([r1, g1, b1]);
      expect(map.points.every((p, i) => i === 0 || p[0] > map.points[i - 1][0])).toBe(true);
    }
    expect(cssGradient(COLORMAPS.rainbow)).toMatch(/^linear-gradient\(to right, rgb/);
  });
  it('produces nice ticks inside the range, including zero', () => {
    expect(niceTicks(-1, 1)).toEqual([-1, -0.5, 0, 0.5, 1]);
    expect(niceTicks(-5, 0)).toContain(0);
    expect(niceTicks(0, 0.0037).every((t) => t >= 0 && t <= 0.0037)).toBe(true);
    expect(niceTicks(3, 3)).toEqual([3]);
    expect(niceTicks(10, 0).length).toBeGreaterThan(2);
  });
});

describe('configuration key', () => {
  it('ignores name and project and the sign of zero', () => {
    const a = { name: 'a', project_id: 'x', flow: { speed: 0, alpha_deg: -0 }, mesh: { b: 1, a: 2 } };
    const b = { name: 'b', project_id: 'y', mesh: { a: 2, b: 1 }, flow: { alpha_deg: 0, speed: -0 } };
    expect(configurationKey(a)).toBe(configurationKey(b));
    expect(configurationKey({ ...a, flow: { speed: 1, alpha_deg: 0 } })).not.toBe(configurationKey(a));
    expect(configurationKey(null)).toBe('');
  });
});

describe('euler (backend sxyz convention)', () => {
  it('matches trimesh euler_matrix(..., "sxyz") exactly', () => {
    for (const c of fixtures as { degrees: number[]; matrix: number[][] }[]) {
      const r = rotation(c.degrees);
      r.forEach((row, i) => row.forEach((v, j) => expect(v).toBeCloseTo(c.matrix[i][j], 9)));
    }
  });
  it('builds column-major transforms and bounds', () => {
    const m = transformMatrix([0, 0, 90], 2, [1, 0, 0]);
    expect(apply(m, [1, 0, 0]).map((v) => Number(v.toFixed(12)))).toEqual([1, 2, 0]);
    const b = transformBounds([[0, 0, 0], [1, 2, 3]], transformMatrix([0, 0, 90]));
    expect(b[0].map((v) => Number(v.toFixed(9)))).toEqual([-2, 0, 0]);
    expect(b[1].map((v) => Number(v.toFixed(9)))).toEqual([0, 1, 3]);
  });
});

describe('evidence wording', () => {
  it('never says validated unless the backend reports exactly that', () => {
    for (const status of ['experimental_validation_pending', 'checks_passed', 'completed', '', undefined, 'Validated', 'validated_pending']) {
      expect(validationLabel(status as string)[0]).toBe('Pending');
    }
    expect(validationLabel('validated')[0]).toBe('Validated');
    const items = evidence({ execution: 'completed', numerical: 'checks_passed', validation: 'experimental_validation_pending', geometry: 'imported' });
    expect(items.find((i) => i.key === 'validation')?.value).toBe('Pending');
    expect(items.some((i) => /validated/i.test(i.value))).toBe(false);
  });
  it('labels experimental physics and wrapped geometry', () => {
    const items = evidence({ physics_maturity: 'experimental', geometry: 'wrapped' });
    expect(items.find((i) => i.key === 'maturity')?.value).toBe('Experimental');
    expect(items.find((i) => i.key === 'geometry')?.value).toBe('Wrapped approximation');
  });
});

describe('theme contrast', () => {
  it('keeps text pairs at WCAG AA', () => {
    for (const [name, foreground, background] of TEXT_PAIRS) {
      expect(contrast(foreground, background), name).toBeGreaterThanOrEqual(4.5);
    }
  });
});
