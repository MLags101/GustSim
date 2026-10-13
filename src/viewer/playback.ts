// Playback interpolates existing streamline points; it never invents a flow field.
export type Track = { t: number; p: number[] }[];

const MAX_TRACKS = 800;

export function tracksFromPolyData(poly: any, limit = MAX_TRACKS) {
  const lines = poly.getLines().getData(), points = poly.getPoints().getData();
  const time = poly.getPointData().getArrayByName('IntegrationTime')?.getData();
  if (!time) return [];
  const tracks: Track[] = [];
  for (let i = 0; i < lines.length && tracks.length < limit;) {
    const count = lines[i++], ids = Array.from(lines.slice(i, i + count)) as number[]; i += count;
    const samples = ids.map((id) => ({ t: Number(time[id]), p: [points[id*3], points[id*3 + 1], points[id*3 + 2]] }))
      .filter((p) => Number.isFinite(p.t)).sort((a, b) => a.t - b.t);
    if (samples.length > 1 && samples.at(-1)!.t > samples[0].t) tracks.push(samples);
  }
  return tracks;
}

/** Position along a track at a fraction `phase` (0..1) of its duration. */
export function sampleTrack(track: Track, phase: number) {
  return sampleAtTime(track, track[0].t + phase*(track.at(-1)!.t - track[0].t));
}

/** Position along a track at integration time `t` (seconds of the steady flow). */
export function sampleAtTime(track: Track, t: number) {
  let low = 0, high = track.length - 1;
  while (high - low > 1) { const mid = (low + high) >> 1; if (track[mid].t < t) low = mid; else high = mid; }
  const a = track[low], b = track[high], f = Math.min(1, Math.max(0, (t - a.t)/(b.t - a.t || 1)));
  return a.p.map((n, i) => n + f*(b.p[i] - n));
}

/** Playback plan: particles released from every seed at even intervals of flow time, moving at
 *  the solved velocity (integration time), with flow time scaled so a typical streamline is
 *  replayed in `seconds`. `perTrack` is the number of particles on a typical streamline; longer
 *  streamlines carry proportionally more so the spacing stays even along every line. */
export function playbackPlan(tracks: Track[], perTrack = 10, seconds = 6, maxParticles = 20000) {
  const durations = tracks.map((track) => track.at(-1)!.t - track[0].t);
  const sorted = [...durations].sort((a, b) => a - b);
  const typical = sorted[Math.floor(sorted.length/2)] || 1;
  let spacing = typical/Math.max(1, perTrack);
  const total = durations.reduce((sum, d) => sum + Math.ceil(d/spacing), 0);
  if (total > maxParticles) spacing *= total/maxParticles;
  const counts = durations.map((d) => Math.max(1, Math.ceil(d/spacing)));
  return { scale: typical/seconds, spacing, counts, total: counts.reduce((a, b) => a + b, 0) };
}

/** Particle positions at wall-clock time `elapsed` (s), written into `out` (3 floats per
 *  particle, `plan.total` particles). A particle that has passed the end of its streamline waits
 *  at the seed until its next release. */
export function particlePositions(tracks: Track[], plan: ReturnType<typeof playbackPlan>, elapsed: number, out: Float32Array) {
  const flowTime = elapsed*plan.scale;
  let k = 0;
  for (let i = 0; i < tracks.length; i++) {
    const track = tracks[i], count = plan.counts[i];
    const start = track[0].t, duration = track.at(-1)!.t - start, cycle = count*plan.spacing;
    // Offset each track so releases are not synchronised across the rake.
    const offset = ((i*0.61803398875) % 1)*plan.spacing;
    for (let j = 0; j < count; j++) {
      const age = (flowTime + offset + j*plan.spacing) % cycle;
      const position = age <= duration ? sampleAtTime(track, start + age) : track[0].p;
      out[k++] = position[0]; out[k++] = position[1]; out[k++] = position[2];
    }
  }
}
