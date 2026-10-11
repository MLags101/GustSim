/** In-page tools for a browser agent (WebMCP, when the browser offers document.modelContext).
 *  A coding agent on the machine uses `gustsim mcp`; both follow the same gates: preview,
 *  explicit confirmation, acknowledged review findings, a reviewed mesh before solving, and
 *  experimental validation always reported as pending. Tool names and schemas are stable. */
import { useEffect } from 'react';
import { create } from 'zustand';
import { api, post } from '../api/client';
import type { Geometry, Run, Simulation, Spec, ValidationReport } from '../api/types';

export interface SetupBridge {
  projectId: string; geometry: Geometry | null; spec: Spec | null; report: ValidationReport | null;
  setIntent: (change: Record<string, unknown>) => void; attest: (change: { dims?: boolean; refs?: boolean }) => void;
  acknowledge: (on: boolean) => void; launch: () => Promise<{ id: string }>;
}
export const useAgentBridge = create<{ setup: SetupBridge | null; simulationId: string | null; publish: (setup: SetupBridge | null) => void; setSimulation: (id: string | null) => void }>((set) => ({
  setup: null, simulationId: null,
  publish: (setup) => set({ setup }),
  setSimulation: (simulationId) => set({ simulationId }),
}));

const empty = { type: 'object', properties: {}, additionalProperties: false };
const acknowledge = { type: 'object', properties: { acknowledge_warnings: { type: 'boolean' } }, additionalProperties: false };

function evidence(run: Run | null | undefined) {
  const result = run?.result || {};
  return {
    id: run?.id ?? null, status: run?.status ?? null, stage: run?.stage ?? null, error: run?.error ?? null, execution: run?.status ?? null,
    termination: result.termination ?? null, numerical_quality: result.numerical_status ?? null,
    experimental_validation: result.validation_status || 'experimental_validation_pending', mesh_check: result.mesh_check ?? null,
    mesh_reviewed: !!(result.reviewed_configuration_id || result.reviewed_mesh_identity), fields_available: !!result.fields_available,
  };
}

async function simulationRuns() {
  const id = useAgentBridge.getState().simulationId;
  if (!id) return { simulation: null as Simulation | null, mesh: null as Run | null, solve: null as Run | null };
  const simulation = await api<Simulation>(`/simulations/${id}`);
  const latest = (kind: string) => simulation.stages.filter((s) => s.kind === kind).at(-1);
  const [mesh, solve] = await Promise.all([latest('mesh') ? api<Run>(`/runs/${latest('mesh')!.id}`) : null, latest('solve') ? api<Run>(`/runs/${latest('solve')!.id}`) : null]);
  return { simulation, mesh, solve };
}

function setup() {
  const bridge = useAgentBridge.getState().setup;
  if (!bridge) throw new Error('Open a setup (Simulations → a draft) first');
  return bridge;
}

export function browserTools() {
  return [
    { name: 'read_gustsim_workspace', title: 'Read CFD workspace', description: 'Read the open model, typed setup, layer fit, mesh, and run. experimental_validation stays pending. This does not start a job.', inputSchema: empty, annotations: { readOnlyHint: true }, execute: async () => {
      const bridge = useAgentBridge.getState().setup;
      const { mesh, solve, simulation } = await simulationRuns();
      return { geometry: bridge?.geometry ? { id: bridge.geometry.id, filename: bridge.geometry.filename, triangles: bridge.geometry.triangles, diagnostics: bridge.geometry.diagnostics, geometry_fidelity: bridge.geometry.geometry_fidelity || 'imported', patches: bridge.geometry.patches.map((p) => ({ name: p.name, triangles: p.triangles })) } : null,
        spec: bridge?.spec ?? null, layer_plan: bridge?.report?.layer_plan ?? null, valid: bridge?.report?.valid ?? null, findings: bridge?.report?.findings ?? [],
        simulation: simulation ? { id: simulation.id, status: simulation.status } : null, mesh: evidence(mesh), run: evidence(solve) };
    } },
    { name: 'validate_gustsim_setup', title: 'Validate simulation setup', description: 'Run preflight on the open setup. Does not queue a mesh or solve. Review findings are not failures; they must be read before a guided run.', inputSchema: empty, annotations: { readOnlyHint: true }, execute: async () => {
      const bridge = setup();
      if (!bridge.spec) throw new Error('The setup is still being generated');
      const report = await post<ValidationReport>('/validate', bridge.spec);
      return { valid: report.valid, errors: report.errors, findings: report.findings, layer_plan: report.layer_plan, configuration_id: report.configuration_id, geometry_ready: report.geometry_ready, validation_status: report.validation_status };
    } },
    { name: 'configure_gustsim_preset', title: 'Configure a guided setup', description: 'Build a pipe, external-object, or rotor setup for the open geometry and show it in the workspace. Does not mesh or solve, and leaves the setup unconfirmed. kind is pipe, aerodynamics, or propeller. For a pipe, pass inlet, outlet, and driving (flow, speed, or pressure). For a rotor, pass patches, origin, axis, and rpm.',
      inputSchema: { type: 'object', properties: { kind: { type: 'string', enum: ['pipe', 'aerodynamics', 'propeller'] }, fluid: { type: 'string', enum: ['air', 'water'] }, turbulence: { type: 'string', enum: ['kOmegaSST', 'laminar'] }, speed: { type: 'number' }, direction: { type: 'array', items: { type: 'number' } }, lift_axis: { type: 'array', items: { type: 'number' } }, inlet: { type: 'string' }, outlet: { type: 'string' }, driving: { type: 'string', enum: ['flow', 'speed', 'pressure'] }, flow_rate: { type: 'number' }, inlet_pressure_pa: { type: 'number' }, outlet_pressure_pa: { type: 'number' }, patches: { type: 'array', items: { type: 'string' } }, origin: { type: 'array', items: { type: 'number' } }, axis: { type: 'array', items: { type: 'number' } }, rpm: { type: 'number' } }, required: ['kind'], additionalProperties: false },
      execute: async (input: Record<string, unknown> = {}) => {
        const bridge = setup();
        if (!bridge.geometry) throw new Error('Import geometry first');
        const preview = await post<{ spec: Spec; report: ValidationReport; review: string[] }>('/presets/preview', { ...input, geometry_id: bridge.geometry.id });
        bridge.setIntent(input);
        bridge.attest({ dims: false, refs: false });
        return { confirmed: false, review: preview.review, valid: preview.report?.valid, findings: preview.report?.findings, layer_plan: preview.report?.layer_plan, spec: preview.spec };
      } },
    { name: 'confirm_gustsim_setup', title: 'Confirm the guided setup', description: 'Set the open guided setup to confirmed after its dimensions, fluid point, and directions have been read. Does not start the solver.', inputSchema: empty, execute: async () => {
      const bridge = setup();
      if (!(bridge.spec as any)?.use_case) throw new Error('Apply a pipe, object, or rotor preset first');
      bridge.attest({ dims: true });
      const report = await post<ValidationReport>('/validate', { ...(bridge.spec as any), use_case: { ...(bridge.spec as any).use_case, confirmed: true } });
      return { valid: report.valid, errors: report.errors, findings: report.findings, layer_plan: report.layer_plan, configuration_id: report.configuration_id };
    } },
    { name: 'queue_gustsim_mesh', title: 'Queue mesh generation', description: 'Queue a new mesh attempt for the open setup. Review findings must be read first; pass acknowledge_warnings true to record that. The worker must be online. This does not solve.', inputSchema: acknowledge, execute: async (input: { acknowledge_warnings?: boolean } = {}) => {
      const bridge = setup();
      if (!bridge.spec) throw new Error('Configure a setup first');
      const report = await post<ValidationReport>('/validate', bridge.spec);
      if (!report.valid) return { queued: false, valid: false, errors: report.errors, findings: report.findings };
      const reviews = report.findings.filter((f) => f.status === 'review');
      if (reviews.length && !input.acknowledge_warnings) return { queued: false, needs_acknowledgement: true, findings: reviews, layer_plan: report.layer_plan };
      try {
        // The agent reviews the mesh explicitly, so the simulation never continues on its own.
        const simulation = await post<Simulation>('/simulations', { spec: bridge.spec, project_id: bridge.projectId, acknowledged: report.configuration_id, plan: { auto_review: false } });
        useAgentBridge.getState().setSimulation(simulation.id);
        const mesh = await api<Run>(`/runs/${simulation.stages[0].id}`);
        return { queued: true, run: evidence(mesh) };
      } catch (error) { return { queued: false, error: error instanceof Error ? error.message : String(error) }; }
    } },
    { name: 'review_gustsim_mesh', title: 'Review the current mesh', description: 'Record review of the open mesh so a solve can use it. If findings need attention, read them and pass acknowledge_warnings true. This does not modify the mesh or start the solver.', inputSchema: acknowledge, execute: async (input: { acknowledge_warnings?: boolean } = {}) => {
      const { mesh } = await simulationRuns();
      if (!mesh) return { reviewed: false, error: 'Queue a mesh first' };
      const findings = mesh.result?.mesh_summary?.findings || [];
      if (findings.some((f: any) => f.status === 'review') && !input.acknowledge_warnings) return { reviewed: false, needs_acknowledgement: true, findings };
      const configuration = await post<ValidationReport>('/validate', mesh.spec);
      try {
        const reviewed = await post<Run>(`/runs/${mesh.id}/review?configuration_id=${configuration.configuration_id}&acknowledge_warnings=${!!input.acknowledge_warnings}`);
        return { reviewed: true, run: evidence(reviewed) };
      } catch (error) { return { reviewed: false, error: error instanceof Error ? error.message : String(error) }; }
    } },
    { name: 'queue_gustsim_solve', title: 'Queue the solve', description: 'Queue a new solve on the reviewed mesh. A geometry or physics change blocks it. Pass acknowledge_warnings true only after reading setup findings. Completion is not experimental validation.', inputSchema: acknowledge, execute: async (input: { acknowledge_warnings?: boolean } = {}) => {
      const id = useAgentBridge.getState().simulationId;
      const { mesh, solve } = await simulationRuns();
      if (!id || !mesh) return { queued: false, error: 'Queue and review a mesh first' };
      if (solve) return { queued: true, run: evidence(solve), experimental_validation: 'pending' };
      if (!(mesh.result?.reviewed_configuration_id || mesh.result?.reviewed_mesh_identity)) return { queued: false, error: 'Review this mesh before solving' };
      try {
        const simulation = await post<Simulation>(`/simulations/${id}/continue?acknowledge_warnings=${!!input.acknowledge_warnings}`);
        const run = await api<Run>(`/runs/${simulation.stages.filter((s) => s.kind === 'solve').at(-1)!.id}`);
        return { queued: true, run: evidence(run), experimental_validation: 'pending' };
      } catch (error) { return { queued: false, error: error instanceof Error ? error.message : String(error) }; }
    } },
    { name: 'read_gustsim_run', title: 'Read the open run', description: 'Read execution, numerical quality, and experimental validation for the open mesh or solve. ok is not a field here: status completed still has experimental validation pending. Histories are omitted.', inputSchema: { type: 'object', properties: { which: { type: 'string', enum: ['solve', 'mesh'] } }, additionalProperties: false }, annotations: { readOnlyHint: true }, execute: async (input: { which?: string } = {}) => {
      const { mesh, solve } = await simulationRuns();
      const run = input.which === 'mesh' ? mesh : solve;
      if (!run) return { available: false };
      return { available: true, ...evidence(run), metrics: run.result?.metrics ?? null, findings: run.result?.findings || run.result?.mesh_summary?.findings || [] };
    } },
  ];
}

/** Registers the tools once; each reads live state when it runs. */
export function BrowserTools() {
  useEffect(() => {
    const context = (document as any).modelContext;
    if (!context?.registerTool) return;
    const life = new AbortController();
    for (const tool of browserTools()) {
      try { Promise.resolve(context.registerTool(tool, { signal: life.signal })).catch(() => undefined); } catch { /* unsupported */ }
    }
    return () => life.abort();
  }, []);
  return null;
}
