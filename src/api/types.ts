/** Response types. Request bodies come from the generated OpenAPI schema; documents the API
 *  returns as plain dicts (runs, manifests, forces, insights) are described here. */
import type { components } from './schema.gen';

export type Spec = components['schemas']['SimulationSpec'];
export type PresetRequest = components['schemas']['PresetRequest'];
export type Vec3 = [number, number, number];

export type FindingStatus = 'pass' | 'fail' | 'review' | 'info' | 'unavailable' | 'warning';
export interface Finding { code: string; status: FindingStatus; detail: string; control?: string; stage?: string; log?: string }

export interface ValidationReport {
  valid: boolean; errors: string[]; warnings: string[]; findings: Finding[];
  configuration_id: string | null; geometry_ready: boolean; validation_status: string;
  reynolds?: number; estimated_mach?: number; layer_plan?: { detail: string; fits: boolean; requested_layers: number; feasible_layers: number };
  capabilities?: string[]; experimental_features?: string[]; physics_maturity?: 'core' | 'experimental';
  resolved_spec?: Spec;
}

export interface Part { id: string; parent_id?: string | null; name: string; patches: string[]; placement?: number[][] }
export interface Patch { name: string; group: number; triangles: number }
export interface Geometry {
  id: string; parent_id: string | null; filename: string; sha256: string; units: string; units_confirmed: boolean;
  bounds: [Vec3, Vec3]; dimensions: Vec3; triangles: number; vertices: number; patches: Patch[]; parts?: Part[];
  operations: any[]; diagnostics: Record<string, any>; geometry_fidelity?: 'wrapped' | 'imported'; wrap?: { detail?: string };
  patch_mapping?: Record<string, string[]>; cad_valid?: boolean; sample?: string;
}
export interface SceneData { points: number[]; polys: number[]; groups: number[]; patches?: { name: string; group: number }[] }

export type RunStatus = 'queued' | 'running' | 'generating' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
export interface Run {
  id: string; created: number; updated: number; status: RunStatus; stage: string; kind: string; spec: any; result: any;
  study_id?: string | null; parent_id?: string | null; error?: string | null; lane?: string; simulation_id?: string | null;
  attempt?: number; retry_of?: string | null; retry_reason?: string | null; started?: number | null; finished?: number | null;
  configuration_id?: string | null; progress?: { percent: number; label: string; basis: string; convergence?: any };
}

export interface StatusBlock { geometry: string; execution: string; numerical: string; validation: string; physics_maturity: 'core' | 'experimental' }
export type SimulationStatus = 'queued' | 'meshing' | 'awaiting_review' | 'solving' | 'postprocessing' | 'completed' | 'failed' | 'cancelled';
export type StudyPath = 'flow.speed' | 'flow.alpha_deg' | 'flow.beta_deg' | 'environment.ride_height_m' | 'rotation.rpm' | 'mesh.tier';
export interface BatchSummary { id: string; name: string; created: number; kind: string }
export interface BatchDetail {
  id: string; name: string; kind: string; created: number;
  definition: { kind: 'sweep' | 'grid' | 'variants'; parameters: { path: StudyPath; values: (number | string)[] }[] };
  members: Simulation[]; shared_meshes: string[];
  series: { x: StudyPath; y: string; points: { x: number | string; y: number; simulation_id: string }[] }[];
}
export interface StageRecord { id: string; kind: string; status: RunStatus; stage: string; attempt: number; retry_of?: string | null; retry_reason?: string | null; created: number; finished?: number | null; error?: string | null }
export interface Simulation {
  id: string; project_id: string | null; name: string; tags: string[]; archived: boolean; created: number; updated: number;
  status: SimulationStatus; stage: string; plan: Record<string, any>;
  geometry: { id: string; filename: string; fidelity: string };
  setup_summary: { use_case: string; mode: string; fluid: string; speed_m_s: number; turbulence: string; tier: string; ground: string; transient: boolean; design?: boolean; elements: Record<string, number> };
  experimental: string[]; stages: StageRecord[]; headline: Record<string, number | null>; cells: number | null; convergence: string | null;
  thumbnail_url: string | null; results_run_id: string | null; current_run_id: string | null; error: string | null; status_block: StatusBlock;
  batch?: { label?: string; values?: Record<string, any>; mesh_from?: string | null };
}

export interface ArrayInfo { name: string; association: 'point' | 'cell'; components: number; range: [number, number] | null; unit?: string | null; label?: string; colormap?: string; default_range?: [number, number]; diverging?: boolean; categorical?: boolean }
export interface Layer {
  id: string; kind: 'surface' | 'lines' | 'isosurface' | 'streamlines' | 'section_stack' | 'animation'; url?: string; bytes?: number; cells?: number; points?: number;
  /** Transient animation: per-frame cell values (frames x cells, float32 little-endian) at `url`. */
  frames?: { times_s: number[]; url: string; array: string; association: 'cell'; count: number; values_per_frame: number; unit: string | null; range: [number, number]; bytes: number };
  arrays: ArrayInfo[]; default?: boolean; iso?: { array: string; value: number; normalized?: number; unit?: string }; color_by?: string;
  orientation?: 'horizontal' | 'vertical'; offset?: number; axis?: 'x' | 'y' | 'z'; maturity?: string;
  items?: { index: number; position_m: number; url: string; bytes: number; cells: number }[];
  blobs?: { area_m2: number; centroid: Vec3; min: Vec3; max: Vec3 }[]; patch_names?: string[];
}
export interface CameraPreset { position: Vec3; focal_point: Vec3; view_up: Vec3 }
export interface Manifest {
  schema: string; run_id: string; simulation_id: string | null; status: StatusBlock & { capabilities: string[] };
  reference: { rho: number; u_inf: Vec3; speed_m_s: number; q_inf_pa: number; area_m2: number; length_m: number; frames: { wind: Record<'drag' | 'side' | 'lift', Vec3> }; definitions: Record<string, string> };
  bounds: { model: [Vec3, Vec3]; domain: [Vec3, Vec3] }; parts: { index: number; id: string; name: string; patches: string[] }[];
  camera_presets: Record<string, CameraPreset>; layers: Layer[]; renders: { id: string; view: string; layer: string; url: string }[];
  thumbnail: string | null; documents: Record<string, string | null>; missing: { id: string; reason: string }[];
  budget: { initial_bytes: number; limit_bytes: number; within: boolean };
  /** 'time_averaged' for transient runs (fieldAverage means), otherwise the steady solution. */
  basis?: 'steady' | 'time_averaged' | 'final_instant';
}

export interface ForceBlock { force_n: Record<string, number>; pressure_n: Record<string, number>; viscous_n: Record<string, number>; porous_n: Record<string, number>; moment_nm: Record<string, number>; std_n: Record<string, number> }
export interface Forces {
  averaging: { method: string; start: number | null; end: number | null; samples: number; basis: string };
  frames: { wind: { axes: Record<string, Vec3>; total: ForceBlock }; object: { axes: Record<string, Vec3>; total: ForceBlock; basis: string } };
  coefficients: Record<string, number | null>; lift_split: { front_n: number; rear_n: number; wheelbase_m: number; basis: string } | null;
  blockage: { ratio: number; method: string; cd_corrected: number | null; cl_corrected: number | null } | null;
  reference: { area_m2: number; length_m: number; dynamic_pressure_pa: number; speed_m_s: number; density: number; confirmed: boolean };
  frontal_area_m2: number | null;
  parts: { name: string; group: string; patches: string[]; wind: Record<string, number>; object: Record<string, number>; cd_contribution: number | null; share_of_drag: number | null }[];
  parts_sum_check: { residual_fraction: number; ok: boolean; note: string } | null;
  speed_curve: { assumption: string; points: { speed_m_s: number; drag_n: number; power_w: number }[] } | null;
  unavailable: Record<string, string>;
}
export interface Insight {
  id: string; rule: string; category: string; severity: 'warning' | 'suggestion' | 'info'; confidence: 'measured' | 'heuristic';
  title: string; message: string; evidence: Record<string, { value: number; unit: string }>;
  anchor: { point: Vec3; normal?: Vec3; radius_m: number; part: string | null };
  view_preset: { camera: CameraPreset; layers: string[]; array: string; range: [number, number] }; limitations: string;
}
export interface Insights { engine_version: string; disclaimer: string; insights: Insight[]; thresholds: Record<string, any> }
export interface Convergence { state: string; detected_at: number | null; averaging: { start: number; end: number; samples: number } | null; message: string; signals: Record<string, any>; stop?: any }
export interface TransientStats {
  state: 'converged' | 'review' | 'incomplete' | 'unavailable'; message: string;
  plan: { start: number; end: number; duration_s: number; average_start: number; frames: number; flow_throughs: number; convective_times: number; max_courant: number };
  window?: { start: number; end: number; start_s: number; end_s: number }; duration_reached_s?: number;
  statistics?: Record<string, { mean: number; std: number; min: number; max: number; half_window_change: number }>;
  spectra?: Record<string, { frequency_hz: number[]; psd: number[] }>; cross_flow?: string;
  dominant_frequency_hz?: number | null; strouhal?: number | null; periods_in_window?: number | null;
  history?: { t_s: number[]; drag_n: number[]; lift_n: number[]; side_n: number[] };
}
export interface MonitorData { forces: Record<string, number>[]; residuals: Record<string, number>[]; convergence: Convergence | null }

export interface TierEstimate {
  tier: string; cells: { low: number; expected: number; high: number }; memory_gb: { mesh: number; solve: number };
  runtime_min: { mesh: number; solve: number; post: number }; iterations: number; time_steps?: number | null; feasible: boolean; reason: string | null;
}
export interface Estimate { basis: string; samples: number; current: TierEstimate; tiers: TierEstimate[]; refinement_boxes: { name: string; min: Vec3; max: Vec3; level: number; cell_size_m: number }[]; surface_cell_m: number; first_layer_m: number; note: string }

export interface CapabilityInfo { label: string; kind: string; maturity: string; gates: Record<string, string>; available: boolean | null; reason: string | null }
export interface Capabilities { physics: Record<string, CapabilityInfo>; lanes: Record<string, { online: boolean }>; features: { async_import: boolean; formats: string[] } }
export interface Health { version: string; openfoam_version: string; worker: { online: boolean; active_run: string | null; lanes: Record<string, { online: boolean; active_run: string | null }> }; cad_available: boolean; async_import: boolean }

export interface Project { id: string; name: string; updated: number; workspace?: Workspace }
export interface Workspace { version?: number; spec?: Spec | null; ack?: string; selected?: string[]; tier?: string; simulations?: string[] }
export interface Sample { id: string; name: string; description: string; use_case: string; speed: number; source: string }

export interface CompareResult {
  baseline: string; columns: { id: string; label: string; unit: string; group: string }[];
  rows: { run_id: string; simulation_id: string | null; name: string; fidelity: string; tier: string; status: Record<string, string>; values: Record<string, number | null>; deltas: Record<string, { abs: number; rel: number | null } | null> }[];
  config_diff: { path: string; values: unknown[] }[]; warnings: { code: string; detail: string }[]; comparable: boolean; note: string;
}
