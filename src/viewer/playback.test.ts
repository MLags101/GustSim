import { describe, expect, it } from 'vitest';
import { particlePositions, playbackPlan, sampleAtTime, type Track } from './playback';

// A straight streamline along x at 2 m/s for 1 s, and a twice-as-long one.
const line = (length: number, speed = 2): Track => Array.from({ length: 11 }, (_, i) => ({ t: (i / 10) * length / speed, p: [(i / 10) * length, 0, 0] }));

describe('streamline playback', () => {
  it('interpolates by integration time', () => {
    expect(sampleAtTime(line(2), 0.25)[0]).toBeCloseTo(0.5);
    expect(sampleAtTime(line(2), 5)[0]).toBeCloseTo(2);
  });

  it('spaces particles evenly in flow time, with more on longer lines', () => {
    const tracks = [line(2), line(4)];
    const plan = playbackPlan(tracks, 8);
    expect(plan.counts[1]).toBeGreaterThan(plan.counts[0]);
    expect(plan.total).toBe(plan.counts[0] + plan.counts[1]);
    const out = new Float32Array(plan.total * 3);
    particlePositions(tracks, plan, 0.37, out);
    const xs = Array.from({ length: plan.counts[0] }, (_, j) => out[j * 3]).sort((a, b) => a - b);
    const gaps = xs.slice(1).map((x, i) => x - xs[i]).filter((gap) => gap > 1e-6);
    // Equal flow-time spacing on a constant-speed line means equal distances.
    for (const gap of gaps) expect(gap).toBeCloseTo(gaps[0], 4);
    // Every particle stays on its streamline.
    for (let k = 0; k < plan.total; k++) { expect(out[k * 3 + 1]).toBe(0); expect(out[k * 3]).toBeGreaterThanOrEqual(0); }
  });

  it('caps the particle count', () => {
    const tracks = Array.from({ length: 50 }, () => line(2));
    const plan = playbackPlan(tracks, 40, 6, 500);
    expect(plan.total).toBeLessThanOrEqual(550);
  });
});
