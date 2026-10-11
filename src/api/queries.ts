import { useQuery, useQueryClient, QueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { api } from './client';
import type { BatchDetail, BatchSummary, Capabilities, CompareResult, Convergence, Estimate, Forces, Geometry, Health, Insights, Manifest, MonitorData, Project, TransientStats, Run, Sample, SceneData, Simulation, Spec, ValidationReport } from './types';

export const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1, staleTime: 2000 } },
});

export const qk = {
  health: ['health'] as const,
  capabilities: ['capabilities'] as const,
  projects: ['projects'] as const,
  project: (id: string) => ['project', id] as const,
  simulations: (archived: boolean) => ['simulations', archived] as const,
  simulation: (id: string) => ['simulation', id] as const,
  batches: ['batches'] as const,
  batch: (id: string) => ['batch', id] as const,
  geometry: (id: string) => ['geometry', id] as const,
  geometries: ['geometries'] as const,
  scene: (id: string) => ['scene', id] as const,
  run: (id: string) => ['run', id] as const,
  runs: ['runs'] as const,
  document: (runId: string, name: string) => ['document', runId, name] as const,
  monitor: (runId: string) => ['monitor', runId] as const,
  validate: (key: string) => ['validate', key] as const,
  estimate: (key: string) => ['estimate', key] as const,
  samples: ['samples'] as const,
  compare: (ids: string) => ['compare', ids] as const,
};

const ACTIVE = new Set(['queued', 'running', 'generating', 'meshing', 'solving', 'postprocessing']);
export const isActive = (status?: string) => !!status && ACTIVE.has(status);

export function useHealth() {
  return useQuery({ queryKey: qk.health, queryFn: () => api<Health>('/health'), refetchInterval: 4000, refetchIntervalInBackground: false });
}
export function useCapabilities() {
  return useQuery({ queryKey: qk.capabilities, queryFn: () => api<Capabilities>('/capabilities'), staleTime: 30000 });
}
export function useProjects() {
  return useQuery({ queryKey: qk.projects, queryFn: () => api<Project[]>('/projects') });
}
export function useProject(id?: string) {
  return useQuery({ queryKey: qk.project(id || ''), queryFn: () => api<Project>(`/projects/${id}`), enabled: !!id, staleTime: Infinity });
}
export function useSimulations(archived = false) {
  return useQuery({
    queryKey: qk.simulations(archived), queryFn: () => api<Simulation[]>(`/simulations${archived ? '?archived=true' : ''}`),
    refetchInterval: (query) => (query.state.data?.some((s) => isActive(s.status)) ? 3000 : 15000),
  });
}
export function useBatches() {
  return useQuery({ queryKey: qk.batches, queryFn: () => api<BatchSummary[]>('/batches'), staleTime: 10_000 });
}
export function useBatch(id?: string) {
  return useQuery({
    queryKey: qk.batch(id || ''), queryFn: () => api<BatchDetail>(`/batches/${id}`), enabled: !!id,
    refetchInterval: (query) => (query.state.data?.members.some((m) => isActive(m.status) || m.status === 'awaiting_review') ? 4000 : false),
  });
}
export function useSimulation(id?: string) {
  return useQuery({
    queryKey: qk.simulation(id || ''), queryFn: () => api<Simulation>(`/simulations/${id}`), enabled: !!id,
    refetchInterval: (query) => (isActive(query.state.data?.status) || query.state.data?.status === 'awaiting_review' ? 2500 : false),
  });
}
export function useGeometry(id?: string | null) {
  return useQuery({ queryKey: qk.geometry(id || ''), queryFn: () => api<Geometry>(`/geometry/${id}`), enabled: !!id, staleTime: Infinity });
}
export function useGeometries() {
  return useQuery({ queryKey: qk.geometries, queryFn: () => api<Geometry[]>('/geometry') });
}
export function useScene(id?: string | null) {
  // Geometry revisions are immutable, so their scene never changes.
  return useQuery({ queryKey: qk.scene(id || ''), queryFn: () => api<SceneData>(`/geometry/${id}/scene`), enabled: !!id, staleTime: Infinity, gcTime: 5 * 60_000 });
}
export function useRun(id?: string | null) {
  return useQuery({
    queryKey: qk.run(id || ''), queryFn: () => api<Run>(`/runs/${id}`), enabled: !!id,
    refetchInterval: (query) => (isActive(query.state.data?.status) ? 2500 : false),
  });
}
export function useRunList() {
  return useQuery({ queryKey: qk.runs, queryFn: () => api<Run[]>('/runs'), refetchInterval: 5000 });
}
export function useDocument<T>(runId: string | null | undefined, name: 'manifest' | 'forces' | 'insights' | 'elements' | 'convergence' | 'transient' | 'design', enabled = true) {
  return useQuery({
    queryKey: qk.document(runId || '', name), queryFn: () => api<T>(`/runs/${runId}/results/${name}`), enabled: !!runId && enabled,
    retry: false, staleTime: 60_000,
  });
}
export const useManifest = (runId?: string | null, enabled = true) => useDocument<Manifest>(runId, 'manifest', enabled);
export const useForces = (runId?: string | null) => useDocument<Forces>(runId, 'forces');
export const useInsights = (runId?: string | null) => useDocument<Insights>(runId, 'insights');
export const useConvergence = (runId?: string | null) => useDocument<Convergence>(runId, 'convergence');
export const useTransient = (runId?: string | null, enabled = true) => useDocument<TransientStats>(runId, 'transient', enabled);
export function useMonitor(runId?: string | null, live = false) {
  return useQuery({ queryKey: qk.monitor(runId || ''), queryFn: () => api<MonitorData>(`/runs/${runId}/monitor`), enabled: !!runId, refetchInterval: live ? 4000 : false });
}
export function useSamples() {
  return useQuery({ queryKey: qk.samples, queryFn: () => api<Sample[]>('/samples'), staleTime: Infinity });
}
export function useCompare(runIds: string[]) {
  const key = runIds.join(',');
  return useQuery({ queryKey: qk.compare(key), queryFn: () => api<CompareResult>(`/compare?runs=${key}`), enabled: runIds.length >= 2 });
}

/** Debounced validation keyed by the configuration content. */
export function useValidation(spec: Spec | null | undefined, key: string) {
  const [debounced, setDebounced] = useState(key);
  useEffect(() => { const t = setTimeout(() => setDebounced(key), 400); return () => clearTimeout(t); }, [key]);
  return useQuery({
    queryKey: qk.validate(debounced), enabled: !!spec && debounced === key,
    queryFn: () => api<ValidationReport>('/validate', { method: 'POST', body: JSON.stringify(spec) }), staleTime: 30_000,
    placeholderData: (previous) => previous,
  });
}
export function useEstimate(spec: Spec | null | undefined, key: string) {
  const [debounced, setDebounced] = useState(key);
  useEffect(() => { const t = setTimeout(() => setDebounced(key), 600); return () => clearTimeout(t); }, [key]);
  return useQuery({
    queryKey: qk.estimate(debounced), enabled: !!spec && debounced === key,
    queryFn: () => api<Estimate>('/mesh/estimate', { method: 'POST', body: JSON.stringify(spec) }), staleTime: 60_000,
    placeholderData: (previous) => previous,
  });
}

/** Server-sent events for one job: a bounded log plus typed events (residuals, forces,
 *  convergence). On completion the job and its simulation queries are refreshed. */
export function useRunEvents(runId?: string | null, onTyped?: (event: any) => void) {
  const client = useQueryClient();
  const [log, setLog] = useState<string[]>([]);
  const typed = useRef(onTyped);
  typed.current = onTyped;
  useEffect(() => {
    setLog([]);
    if (!runId || typeof EventSource === 'undefined') return;
    const source = new EventSource(`/api/runs/${runId}/events`);
    source.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data);
        if (event.type) typed.current?.(event);
        else if (event.message) setLog((lines) => [...lines, String(event.message)].slice(-250));
      } catch { /* ignore malformed */ }
    };
    source.addEventListener('done', () => {
      source.close();
      client.invalidateQueries({ queryKey: qk.run(runId) });
      client.invalidateQueries({ queryKey: ['simulation'] });
    });
    return () => source.close();
  }, [runId, client]);
  return log;
}
