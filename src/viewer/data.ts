import vtkXMLPolyDataReader from '@kitware/vtk.js/IO/XML/XMLPolyDataReader';
import { isShare, omittedReason, shareFile } from '../share/bundle';

/** URL-keyed LRU of parsed result layers, bounded by bytes (the browser budget for result
 *  data is 40 MB per view; a little more is kept so flipping between layers is instant). */
const BUDGET = 96 * 1024 * 1024;
interface Entry { poly: any; bytes: number; used: number }
const cache = new Map<string, Entry>();
const pending = new Map<string, Promise<any>>();
let total = 0;

function evict() {
  while (total > BUDGET && cache.size > 1) {
    let oldest: string | null = null;
    let when = Infinity;
    for (const [key, entry] of cache) if (entry.used < when) { when = entry.used; oldest = key; }
    if (!oldest) break;
    const entry = cache.get(oldest)!;
    cache.delete(oldest);
    total -= entry.bytes;
    // Actors may still reference it; vtk objects are released when unreferenced.
  }
}

export async function loadPolyData(url: string): Promise<any> {
  const hit = cache.get(url);
  if (hit) { hit.used = performance.now(); return hit.poly; }
  const inflight = pending.get(url);
  if (inflight) return inflight;
  const promise = (async () => {
    let bytes: ArrayBuffer;
    if (isShare) {
      const embedded = shareFile(url);
      if (!embedded) throw new Error(omittedReason(url) || 'This result layer is not included in the shared report');
      bytes = embedded;
    } else {
      const response = await fetch(url);
      if (!response.ok) throw new Error(response.status === 404 ? 'This result layer is not available' : `Could not load ${url} (${response.status})`);
      bytes = await response.arrayBuffer();
    }
    const reader = vtkXMLPolyDataReader.newInstance();
    reader.parseAsArrayBuffer(bytes);
    const poly = reader.getOutputData(0);
    if (!poly) throw new Error('The result file could not be parsed');
    cache.set(url, { poly, bytes: bytes.byteLength, used: performance.now() });
    total += bytes.byteLength;
    evict();
    return poly;
  })();
  pending.set(url, promise);
  try {
    return await promise;
  } finally {
    pending.delete(url);
  }
}

/** Warm the cache (section neighbours, the next frame) without waiting. */
export function prefetch(urls: string[]) {
  for (const url of urls) if (!cache.has(url) && !pending.has(url)) loadPolyData(url).catch(() => undefined);
}

export function arrayInfo(poly: any, name: string): { association: 'point' | 'cell'; array: any } | null {
  const point = poly?.getPointData().getArrayByName(name);
  if (point) return { association: 'point', array: point };
  const cell = poly?.getCellData().getArrayByName(name);
  if (cell) return { association: 'cell', array: cell };
  return null;
}

/** Range of an array (vector arrays use magnitude), ignoring non-finite values. */
export function arrayRange(array: any): [number, number] | null {
  if (!array) return null;
  const range = array.getRange(array.getNumberOfComponents() > 1 ? -1 : 0);
  if (!range || !Number.isFinite(range[0]) || !Number.isFinite(range[1])) return null;
  return [range[0], range[1]];
}

/** Robust colour range: 2nd–98th percentile, so a few extreme cells do not wash out the map. */
export function percentileRange(array: any, low = 0.02, high = 0.98): [number, number] | null {
  if (!array) return null;
  const values = array.getData() as ArrayLike<number>;
  const components = array.getNumberOfComponents();
  const n = values.length / components;
  if (!n) return null;
  const stride = Math.max(1, Math.floor(n / 50000));
  const sample: number[] = [];
  for (let i = 0; i < n; i += stride) {
    let v = 0;
    if (components === 1) v = values[i];
    else { for (let c = 0; c < components; c++) v += values[i * components + c] ** 2; v = Math.sqrt(v); }
    if (Number.isFinite(v)) sample.push(v);
  }
  if (!sample.length) return null;
  sample.sort((a, b) => a - b);
  const at = (q: number) => sample[Math.min(sample.length - 1, Math.max(0, Math.round(q * (sample.length - 1))))];
  return [at(low), at(high)];
}
