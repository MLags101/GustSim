import { describe, expect, it } from 'vitest';
import { orient } from './RotorCard';

describe('rotor orientation (mirrors rotors.orient)', () => {
  it('points the axis along the pushed air and keeps the physical spin', () => {
    expect(orient([0, 0, 1], 6000, 1)).toEqual({ axis: [0, 0, -1], rpm: -6000 });
    expect(orient([0, 0, 1], -6000, 1)).toEqual({ axis: [0, 0, 1], rpm: -6000 });
    expect(orient([0, 0, 1], 6000, -1)).toEqual({ axis: [0, 0, 1], rpm: 6000 });
  });
  it('leaves the axis alone when the blade sense is unknown', () => {
    expect(orient([1, 0, 0], 3000, 0)).toEqual({ axis: [1, 0, 0], rpm: 3000 });
  });
});
