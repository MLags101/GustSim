import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiError, api, post, put } from '../../api/client';
import { qk, useCapabilities, useEstimate, useGeometry, useHealth, useProject, useScene, useValidation } from '../../api/queries';
import type { Geometry, SceneData, Spec, Vec3 } from '../../api/types';
import { configurationKey } from '../../lib/configurationKey';

export type UseCase = 'aerodynamics' | 'propeller' | 'pipe';
export type Tier = 'basic' | 'regular' | 'advanced' | 'custom';
export interface Intent {
  kind: UseCase; fluid: 'air' | 'water' | 'multiphase'; turbulence: 'kOmegaSST' | 'laminar'; speed: number;
  direction: Vec3; lift_axis: Vec3; inlet: string; outlet: string; driving: 'flow' | 'speed' | 'pressure';
  flow_rate: number; inlet_pressure_pa: number; outlet_pressure_pa: number; patches: string[];
  origin: Vec3; axis: Vec3; rpm: number; tier: Tier;
  ground?: 'none' | 'static' | 'moving' | 'slip'; ride_height_m?: number; waterline_z?: number;
  /** 'auto': axis and centre follow detection from the selected rotating parts; 'manual': user-set. */
  axis_mode?: 'auto' | 'manual';
}
export interface WheelElement { name: string; patches: string[]; center: Vec3; axis: Vec3; radius: number; rotating: boolean }
export interface PorousElement { name: string; center: Vec3; axis: Vec3; up: Vec3; width_m: number; height_m: number; thickness_m: number; curve: [[number, number], [number, number]]; transverse_factor?: number }
export interface OpeningElement { patch: string; kind: 'inflow_velocity' | 'inflow_flow_rate' | 'total_pressure' | 'outflow_flow_rate' | 'outflow_pressure'; value: number }
export interface ZoneElement { name: string; kind?: 'propeller' | 'fan' | 'rim'; origin: Vec3; axis: Vec3; rpm: number; radius: number; length: number; blade_radius: number; patches: string[] }
export interface DesignBoxDraft { name: string; center: Vec3; dimensions: Vec3; max_displacement_m: number }
export interface DesignDraft { enabled: boolean; objective: 'drag' | 'lift'; iterations: number; cycles: number }
export interface ElementsDraft { wheels?: WheelElement[]; porous?: PorousElement[]; openings?: OpeningElement[]; rotating_zones?: ZoneElement[]; design_boxes?: DesignBoxDraft[] }
export interface Overrides {
  fluid_point?: Vec3; reference_area?: number; reference_length?: number;
  iterations?: number; processes?: number; timeout_minutes?: number; auto_retry?: number;
  mesh?: { base_cells?: number; surface_level?: number; feature_level?: number; layers?: number; max_cells?: number; body_level?: number };
  elements?: ElementsDraft; front_axle?: Vec3 | null; rear_axle?: Vec3 | null; design?: DesignDraft;
  time?: TimeDraft;
  /** Experimental: stop the solve once the forces have converged (spec.convergence.enabled). */
  early_stop?: boolean;
}
/** Transient settings (spec.time). Durations are in domain flow-throughs unless duration_s is set. */
export interface TimeDraft { mode: 'steady' | 'transient'; flow_throughs?: number; frames?: number; average_after_fraction?: number; max_courant?: number; sliding_mesh?: boolean }
export interface Attestation { key: string; dims: boolean; refs: boolean }
export interface SetupWorkspace { version: 2; geometry_id: string; intent?: Intent; overrides?: Overrides; attestation?: Attestation; ack?: string; simulations?: string[] }

export function patchCentroids(scene: SceneData | undefined): Record<string, Vec3> {
  if (!scene?.patches) return {};
  const sums = new Map<number, [number, number, number, number]>();
  for (let i = 0, cell = 0; i < scene.polys.length; cell++) {
    const count = scene.polys[i];
    const group = scene.groups[cell];
    const entry = sums.get(group) || [0, 0, 0, 0];
    for (let k = 1; k <= count; k++) {
      const p = scene.polys[i + k] * 3;
      entry[0] += scene.points[p]; entry[1] += scene.points[p + 1]; entry[2] += scene.points[p + 2]; entry[3] += 1;
    }
    sums.set(group, entry);
    i += count + 1;
  }
  return Object.fromEntries(scene.patches.map((p) => {
    const s = sums.get(p.group) || [0, 0, 0, 1];
    return [p.name, [s[0] / s[3], s[1] / s[3], s[2] / s[3]] as Vec3];
  }));
}

export function defaultIntent(geometry: Geometry, scene?: SceneData): Intent {
  const names = geometry.patches.map((p) => p.name);
  const center = geometry.bounds[0].map((v, i) => (v + geometry.bounds[1][i]) / 2) as Vec3;
  const pipe = geometry.sample === 'pipe';
  let inlet = '', outlet = '';
  if (pipe || names.length <= 4) {
    const centroids = patchCentroids(scene);
    const axis = geometry.dimensions.indexOf(Math.max(...geometry.dimensions));
    const sorted = names.filter((n) => centroids[n]).sort((a, b) => centroids[a][axis] - centroids[b][axis]);
    if (sorted.length >= 3) { inlet = sorted[0]; outlet = sorted[sorted.length - 1]; }
  }
  const base: Intent = {
    kind: pipe ? 'pipe' : 'aerodynamics', fluid: 'air', turbulence: 'kOmegaSST', speed: pipe ? 1 : 20,
    direction: [-1, 0, 0], lift_axis: [0, 0, 1], inlet, outlet, driving: 'speed', flow_rate: 0.001,
    inlet_pressure_pa: 100, outlet_pressure_pa: 0, patches: names, origin: center, axis: [1, 0, 0], rpm: 2000, tier: 'basic',
  };
  // A demo model starts from its own ready setup (still fully editable).
  return { ...base, ...((geometry.sample_setup?.intent || {}) as Partial<Intent>) };
}

function presetRequest(geometryId: string, intent: Intent) {
  return { geometry_id: geometryId, kind: intent.kind, turbulence: intent.turbulence, speed: intent.speed,
    direction: intent.direction, lift_axis: intent.lift_axis, inlet: intent.inlet, outlet: intent.outlet, driving: intent.driving,
    flow_rate: intent.flow_rate, inlet_pressure_pa: intent.inlet_pressure_pa, outlet_pressure_pa: intent.outlet_pressure_pa,
    patches: intent.patches, origin: intent.origin, axis: intent.axis, rpm: intent.rpm, tier: intent.tier === 'custom' ? 'custom' : intent.tier,
    fluid: intent.fluid === 'multiphase' ? 'air' : intent.fluid,
    ground: intent.kind === 'aerodynamics' ? intent.ground || 'none' : 'none', ride_height_m: intent.ride_height_m || 0 };
}

function applyOverrides(base: Spec, overrides: Overrides, attestation: Attestation | undefined, previewKey: string, projectId: string, name: string, tier: Tier, intent?: Intent, processes?: number, earlyStopDefault?: boolean): Spec {
  const spec: any = JSON.parse(JSON.stringify(base));
  spec.project_id = projectId;
  spec.name = name || spec.name;
  if (overrides.fluid_point) spec.domain.fluid_point = overrides.fluid_point;
  if (overrides.reference_area) spec.references.area = overrides.reference_area;
  if (overrides.reference_length) spec.references.length = overrides.reference_length;
  if (overrides.iterations) spec.solver.iterations = overrides.iterations;
  // An explicit choice wins; otherwise use the cores the compute worker reports (hardware.py).
  if (overrides.processes) spec.solver.processes = overrides.processes;
  else if (processes) spec.solver.processes = processes;
  if (overrides.timeout_minutes) spec.solver.timeout_minutes = overrides.timeout_minutes;
  if (overrides.time?.mode === 'transient' || overrides.time?.sliding_mesh) spec.time = { ...(spec.time || {}), ...overrides.time };
  if (intent?.fluid === 'multiphase') spec.free_surface = { ...(spec.free_surface || {}), waterline_z: intent.waterline_z ?? 0 };
  else delete spec.free_surface;
  if (overrides.auto_retry !== undefined) spec.solver.auto_retry = overrides.auto_retry;
  // Early stopping: the user's choice, else on by default once the capability is core.
  const earlyStop = overrides.early_stop ?? earlyStopDefault;
  if (earlyStop !== undefined) spec.convergence = { ...(spec.convergence || {}), enabled: earlyStop };
  if (tier === 'custom' && overrides.mesh) Object.assign(spec.mesh, overrides.mesh, { tier: 'custom' });
  const e = overrides.elements || {};
  spec.elements = { ...spec.elements, wheels: e.wheels || [], porous: e.porous || [], openings: e.openings || [],
    rotating_zones: spec.use_case?.kind === 'propeller' ? e.rotating_zones || [] : [], design_boxes: e.design_boxes || [] };
  if (overrides.design?.enabled) spec.design = { ...(spec.design || {}), enabled: true, objective: overrides.design.objective, iterations: overrides.design.iterations, cycles: overrides.design.cycles };
  if (e.openings?.length && spec.use_case) spec.use_case.force_patches = spec.use_case.force_patches.filter((p: string) => !e.openings!.some((o) => o.patch === p));
  if (overrides.front_axle) spec.references.front_axle = overrides.front_axle;
  if (overrides.rear_axle) spec.references.rear_axle = overrides.rear_axle;
  const valid = attestation?.key === previewKey;
  if (spec.use_case) {
    spec.use_case.confirmed = valid && !!attestation?.dims;
    spec.use_case.references_confirmed = valid && !!attestation?.refs;
  }
  return spec as Spec;
}

export function useSetup(projectId: string) {
  const client = useQueryClient();
  const project = useProject(projectId);
  const [workspace, setWorkspace] = useState<SetupWorkspace | null>(null);
  const [name, setName] = useState('');
  const loaded = useRef(false);

  useEffect(() => {
    if (!project.data || loaded.current) return;
    loaded.current = true;
    const w = project.data.workspace as SetupWorkspace | undefined;
    setName(project.data.name);
    if (w?.geometry_id) setWorkspace({ ...w, version: 2 });
    else if ((w as any)?.spec?.geometry_id) setWorkspace({ version: 2, geometry_id: (w as any).spec.geometry_id });
  }, [project.data]);

  const geometry = useGeometry(workspace?.geometry_id);
  const scene = useScene(workspace?.geometry_id);

  // Initialise the intent once geometry and its scene are known.
  useEffect(() => {
    if (!workspace || workspace.intent || !geometry.data || !scene.data) return;
    setWorkspace((w) => (w ? { ...w, intent: defaultIntent(geometry.data!, scene.data),
      overrides: w.overrides || ((geometry.data!.sample_setup?.overrides || {}) as Overrides) } : w));
  }, [workspace, geometry.data, scene.data]);

  // Autosave the draft (debounced).
  useEffect(() => {
    if (!workspace || !loaded.current) return;
    const timer = setTimeout(() => {
      put(`/projects/${projectId}`, { name: name || 'New simulation', workspace }).then(() => client.invalidateQueries({ queryKey: qk.projects })).catch(() => undefined);
    }, 800);
    return () => clearTimeout(timer);
  }, [workspace, name, projectId, client]);

  const intent = workspace?.intent;
  const overrides = workspace?.overrides || {};
  const request = useMemo(() => (workspace && intent ? presetRequest(workspace.geometry_id, intent) : null), [workspace?.geometry_id, intent]);
  const previewKey = request ? JSON.stringify(request) : '';
  const [debouncedKey, setDebouncedKey] = useState(previewKey);
  useEffect(() => { const t = setTimeout(() => setDebouncedKey(previewKey), 350); return () => clearTimeout(t); }, [previewKey]);
  const preview = useQuery({
    queryKey: ['preview', debouncedKey], enabled: !!request && debouncedKey === previewKey,
    queryFn: () => post<{ spec: Spec; report: any; review: string[] }>('/presets/preview', request), retry: false,
    placeholderData: (previous) => previous, staleTime: Infinity,
  });
  const previewError = preview.error instanceof ApiError ? preview.error.message : preview.error ? String(preview.error) : null;

  const health = useHealth();
  const recommendedProcesses = health.data?.worker.lanes?.compute?.capabilities?.hardware?.recommended_processes;
  const capabilities = useCapabilities();
  const earlyStopMaturity = capabilities.data?.physics?.early_stop?.maturity;
  const earlyStopDefault = earlyStopMaturity === 'core' ? true : undefined;
  const spec = useMemo(() => (preview.data?.spec && intent
    ? applyOverrides(preview.data.spec, overrides, workspace?.attestation, previewKey, projectId, name, intent.tier, intent, recommendedProcesses, earlyStopDefault)
    : null), [preview.data, overrides, workspace?.attestation, previewKey, projectId, name, intent?.tier, recommendedProcesses, earlyStopDefault]);
  const specKey = configurationKey(spec);
  const validation = useValidation(spec, specKey);
  const estimate = useEstimate(spec, specKey);
  const report = validation.data;
  const attested = workspace?.attestation?.key === previewKey ? workspace.attestation : { key: previewKey, dims: false, refs: false };
  const reviewNeeded = !!report?.findings.some((f) => f.status === 'review');
  const acknowledged = !reviewNeeded || (!!report?.configuration_id && workspace?.ack === report.configuration_id);

  function update(change: Partial<SetupWorkspace>) { setWorkspace((w) => (w ? { ...w, ...change } : w)); }
  function setIntent(change: Partial<Intent>) { setWorkspace((w) => (w && w.intent ? { ...w, intent: { ...w.intent, ...change } } : w)); }
  function setOverrides(change: Partial<Overrides>) { setWorkspace((w) => (w ? { ...w, overrides: { ...(w.overrides || {}), ...change } } : w)); }
  function attest(change: Partial<Attestation>) { setWorkspace((w) => (w ? { ...w, attestation: { ...attested, ...change, key: previewKey } } : w)); }
  function acknowledge(on: boolean) { update({ ack: on && report?.configuration_id ? report.configuration_id : '' }); }

  /** Switch to another geometry revision (prepared, transformed): keep the intent where it still applies. */
  function switchRevision(next: Geometry) {
    setWorkspace((w) => {
      if (!w) return w;
      const names = new Set(next.patches.map((p) => p.name));
      const map = next.patch_mapping || {};
      const remap = (list: string[]) => [...new Set(list.flatMap((n) => map[n] || (names.has(n) ? [n] : [])))].filter((n) => names.has(n));
      const intentNext = w.intent ? { ...w.intent, patches: w.intent.kind === 'aerodynamics' && !w.intent.patches.length ? [...names] : remap(w.intent.patches),
        inlet: names.has(w.intent.inlet) ? w.intent.inlet : (map[w.intent.inlet]?.[0] || ''), outlet: names.has(w.intent.outlet) ? w.intent.outlet : (map[w.intent.outlet]?.[0] || '') } : undefined;
      if (intentNext && intentNext.kind === 'aerodynamics' && !intentNext.patches.length) intentNext.patches = [...names];
      return { ...w, geometry_id: next.id, intent: intentNext, attestation: undefined, ack: '' };
    });
    client.invalidateQueries({ queryKey: qk.geometries });
  }

  async function launch(): Promise<{ id: string }> {
    if (!spec || !report) throw new Error('The setup is still being checked');
    const body = { spec, name: name || spec.name, project_id: projectId, acknowledged: report.configuration_id };
    const simulation = await post<{ id: string }>('/simulations', body);
    update({ simulations: [...(workspace?.simulations || []), simulation.id] });
    client.invalidateQueries({ queryKey: ['simulations'] });
    return simulation;
  }

  /** Several simulations from this setup: a sweep (one parameter) or a grid. Members that would
   *  mesh identically (speed, RPM) share one mesh. */
  async function launchBatch(name: string, parameters: { path: string; values: (number | string)[] }[]): Promise<{ id: string }> {
    if (!spec || !report) throw new Error('The setup is still being checked');
    const body = { name, base: spec, kind: parameters.length === 1 ? 'sweep' : 'grid', parameters, tags: [],
      auto_review: true, acknowledge_review: reviewNeeded && acknowledged };
    const batch = await post<{ id: string }>(`/batches?project_id=${encodeURIComponent(projectId)}`, body);
    client.invalidateQueries({ queryKey: ['simulations'] });
    client.invalidateQueries({ queryKey: ['batches'] });
    return batch;
  }

  async function caseOnly() {
    if (!spec) throw new Error('The setup is still being checked');
    return post(`/runs?kind=case`, spec);
  }

  return { earlyStopMaturity, project, workspace, name, setName, geometry, scene, intent, overrides, preview, previewError, previewKey, spec, specKey,
    validation, report, estimate, attested, reviewNeeded, acknowledged, update, setIntent, setOverrides, attest, acknowledge, switchRevision, launch, launchBatch, caseOnly,
    refreshGeometry: () => api<Geometry>(`/geometry/${workspace?.geometry_id}`) };
}
export type SetupState = ReturnType<typeof useSetup>;
