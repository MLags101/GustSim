import { isShare, shareDocument } from '../share/bundle';

/** Fetch wrapper for the local GustSim API. Errors carry the HTTP status and FastAPI detail. */
export class ApiError extends Error {
  status: number;
  detail: unknown;
  constructor(status: number, detail: unknown) {
    super(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((d: any) => d?.msg ?? String(d)).join('; ') : JSON.stringify(detail));
    this.status = status;
    this.detail = detail;
  }
}

export async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
  if (isShare) {
    // Offline share bundle: answers come from the embedded documents; nothing can change.
    if ((options.method || 'GET').toUpperCase() !== 'GET') throw new ApiError(403, 'This is a read-only shared report');
    const { found, value } = shareDocument(path);
    if (!found) throw new ApiError(404, 'Not included in this shared report');
    return value as T;
  }
  const headers = new Headers(options.headers);
  if (options.body && !(options.body instanceof FormData)) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail ?? body);
  }
  if (response.status === 204) return undefined as T;
  return response.json();
}

export const post = <T = any>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body instanceof FormData ? body : JSON.stringify(body ?? {}) });
export const put = <T = any>(path: string, body: unknown) => api<T>(path, { method: 'PUT', body: JSON.stringify(body) });
export const patch = <T = any>(path: string, body: unknown) => api<T>(path, { method: 'PATCH', body: JSON.stringify(body) });

/** Download an API artifact through fetch so errors surface instead of an empty tab. */
export async function download(path: string, fallbackName: string) {
  if (isShare) throw new ApiError(403, 'Downloads are not included in a shared report; ask the sender for the files');
  const response = await fetch(`/api${path}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiError(response.status, body.detail);
  }
  const disposition = response.headers.get('content-disposition') || '';
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition);
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = match ? decodeURIComponent(match[1]) : fallbackName;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export const fileUrl = (runId: string, relative: string) => `/api/runs/${runId}/files/${relative}`;
