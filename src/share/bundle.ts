/** Offline share bundle: a single HTML file exported by the API (GET /runs/{id}/export/share)
 *  embeds the app, the API documents the results pages read, and the result files (base64).
 *  In that mode the API client and the result loaders read from the bundle, never the network,
 *  and editing actions are hidden. */
export interface ShareBundle {
  schema: 'gustsim.share/1';
  simulation_id: string;
  run_id: string;
  title: string;
  created: number;
  /** API GET responses keyed by path without the /api prefix, e.g. "/runs/<id>/results/manifest". */
  documents: Record<string, unknown>;
  /** Result files keyed by API URL ("/api/runs/<id>/files/<relative>"), base64-encoded. */
  files: Record<string, string>;
  /** Files left out to respect the size budget, with the reason. */
  omitted: { path: string; reason: string }[];
}

declare global {
  interface Window { __GUSTSIM_SHARE__?: ShareBundle }
}

export const share: ShareBundle | null = typeof window !== 'undefined' ? window.__GUSTSIM_SHARE__ ?? null : null;
export const isShare = share !== null;

const decoded = new Map<string, ArrayBuffer>();

export function shareDocument(path: string): { found: boolean; value: unknown } {
  if (!share) return { found: false, value: undefined };
  const key = path.split('#')[0];
  if (key in share.documents) return { found: true, value: JSON.parse(JSON.stringify(share.documents[key])) };
  const bare = key.split('?')[0];
  return bare in share.documents ? { found: true, value: JSON.parse(JSON.stringify(share.documents[bare])) } : { found: false, value: undefined };
}

export function shareFile(url: string): ArrayBuffer | null {
  if (!share) return null;
  const hit = decoded.get(url);
  if (hit) return hit;
  const encoded = share.files[url];
  if (encoded === undefined) return null;
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  decoded.set(url, bytes.buffer);
  return bytes.buffer;
}

export function omittedReason(url: string): string | null {
  const entry = share?.omitted.find((o) => url.endsWith(o.path));
  return entry ? entry.reason : null;
}
