import { describe, expect, it } from 'vitest';
import { PARAMETERS, parseValues } from './BatchDialog';

describe('batch values', () => {
  it('parses display units into SI and drops duplicates', () => {
    expect(parseValues('36, 72;72  108', 'speed', 'km/h')).toEqual([10, 20, 30]);
    expect(parseValues('0 2.5 5', undefined, undefined)).toEqual([0, 2.5, 5]);
  });
  it('rejects empty or non-numeric entries', () => {
    expect(parseValues('', 'speed', 'm/s')).toBeNull();
    expect(parseValues('10, fast', 'speed', 'm/s')).toBeNull();
  });
  it('offers only parameters that apply to the setup', () => {
    const onGround = { environment: { ground: 'moving' }, rotation: { enabled: false } };
    const paths = PARAMETERS.filter((p) => p.available(onGround)).map((p) => p.path);
    expect(paths).toContain('environment.ride_height_m');
    expect(paths).not.toContain('flow.alpha_deg');
    expect(paths).not.toContain('rotation.rpm');
  });
});
