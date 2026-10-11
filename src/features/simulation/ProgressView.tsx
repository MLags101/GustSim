import { useMemo, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Check, CircleAlert, Loader2, RotateCcw, Square } from 'lucide-react';
import { post } from '../../api/client';
import { isActive, useMonitor, useRun, useRunEvents } from '../../api/queries';
import type { Finding, Run, Simulation } from '../../api/types';
import { SeriesChart } from '../../charts/SeriesChart';
import { Badge, Card, Check as CheckBox, Notice, ProgressBar, cx, significant, toast } from '../../ui';
import { ViewerFrame } from '../../viewer/ViewerFrame';

const STAGES: [string, string][] = [['mesh', 'Mesh'], ['review', 'Mesh check'], ['solve', 'Solve'], ['package', 'Results'], ['completed', 'Report']];

function stageState(simulation: Simulation, key: string): 'done' | 'active' | 'pending' | 'failed' {
  const order = STAGES.map(([k]) => k);
  const current = simulation.status === 'completed' ? 'completed' : simulation.stage;
  const here = order.indexOf(key), at = order.indexOf(current === 'meshing' ? 'mesh' : current);
  if (simulation.status === 'completed') return 'done';
  if (simulation.status === 'failed' && here === at) return 'failed';
  if (here < at) return 'done';
  if (here === at) return 'active';
  return 'pending';
}

export function StageTimeline({ simulation }: { simulation: Simulation }) {
  return <ol className="flex flex-wrap items-center gap-2" aria-label="Simulation stages">
    {STAGES.map(([key, label], i) => {
      const state = stageState(simulation, key);
      return <li key={key} className="flex items-center gap-2">
        <span className={cx('flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold',
          state === 'done' ? 'bg-success-soft text-success' : state === 'active' ? 'bg-accent-soft text-accent-ink' : state === 'failed' ? 'bg-danger-soft text-danger' : 'bg-surface-3 text-muted')}>
          {state === 'done' ? <Check size={13} /> : state === 'active' ? <Loader2 size={13} className="animate-spin" /> : state === 'failed' ? <CircleAlert size={13} /> : <span className="h-1.5 w-1.5 rounded-full bg-current" />}{label}
        </span>
        {i < STAGES.length - 1 && <span className="h-px w-4 bg-line-strong" />}
      </li>;
    })}
  </ol>;
}

function MeshEvidence({ run }: { run: Run }) {
  const summary = run.result?.mesh_summary || {};
  const m = summary.mesh_metrics || {};
  const coverage = summary.layer_coverage;
  const rows: [string, string][] = [
    ['Cells', significant(run.result?.cell_count, 6)],
    ['Max non-orthogonality', m.max_non_orthogonality != null ? `${significant(m.max_non_orthogonality, 3)}°` : '—'],
    ['Max skewness', significant(m.max_skewness, 3)],
    ['Near-wall layers achieved', coverage ? `${significant(coverage.mean_layers, 3)} average (worst ${significant(coverage.min_layers, 3)})` : '—'],
    ['Meshing', summary.method === 'parallel' ? 'Parallel' : summary.method === 'serial_fallback' ? 'Serial (parallel failed)' : summary.method === 'reused' ? 'Reused reviewed mesh' : 'Serial'],
  ];
  return <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">{rows.map(([k, v]) => <div key={k} className="flex justify-between gap-2 border-b border-line py-1"><dt className="text-muted">{k}</dt><dd className="font-medium tabular-nums">{v}</dd></div>)}</dl>;
}

function MeshReview({ simulation, mesh }: { simulation: Simulation; mesh: Run }) {
  const findings: Finding[] = mesh.result?.mesh_summary?.findings || [];
  const reviews = findings.filter((f) => f.status === 'review');
  const blocking = findings.filter((f) => ['fail', 'unavailable'].includes(f.status));
  const [acknowledged, setAcknowledged] = useState(false);
  const [plane, setPlane] = useState('mesh-preview.vtp');
  const client = useQueryClient();
  async function proceed() {
    try { await post(`/simulations/${simulation.id}/continue?acknowledge_warnings=${acknowledged}`); client.invalidateQueries({ queryKey: ['simulation', simulation.id] }); toast.success('Mesh reviewed; the solve is queued.'); }
    catch (e) { toast.error(e); }
  }
  return <Card title="Check the mesh" actions={<Badge tone="warn">Waiting for your review</Badge>}>
    <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
      <div className="space-y-2">
        <ViewerFrame className="h-80 rounded-xl border border-line" bounds={null}
          layers={[{ id: 'mesh', url: `/api/runs/${mesh.id}/files/${plane}`, edges: true, color: [0.8, 0.85, 0.9] }]} />
        <div className="flex flex-wrap gap-1">{[['mesh-preview.vtp', 'Section Y'], ['mesh-section-x.vtp', 'Section X'], ['mesh-section-z.vtp', 'Section Z'], ['mesh-exterior.vtp', 'Outer surface']].map(([file, label]) =>
          <button key={file} className={cx('btn-ghost btn-sm', plane === file && 'bg-surface-3')} onClick={() => setPlane(file)}>{label}</button>)}</div>
      </div>
      <div className="space-y-3">
        <MeshEvidence run={mesh} />
        <ul className="space-y-1.5">{findings.filter((f) => f.status !== 'pass').map((f) => <li key={f.code} className="text-sm"><span className={cx('mr-1.5 text-xs font-bold uppercase', f.status === 'review' ? 'text-warning' : f.status === 'fail' ? 'text-danger' : 'text-muted')}>{f.status}</span>{f.detail}</li>)}</ul>
        {blocking.length ? <Notice tone="bad">The mesh is missing required evidence; it cannot be used. Retry with refined settings.</Notice> : <>
          {reviews.length > 0 && <CheckBox checked={acknowledged} onChange={setAcknowledged}>I inspected the mesh and the findings above.</CheckBox>}
          <button className="btn-primary w-full" disabled={reviews.length > 0 && !acknowledged} onClick={proceed}>Use this mesh and solve</button>
        </>}
      </div>
    </div>
  </Card>;
}

function LiveCharts({ runId, live }: { runId: string; live: boolean }) {
  const monitor = useMonitor(runId, live);
  const [convergence, setConvergence] = useState<any>(null);
  useRunEvents(runId, (event) => { if (event.type === 'convergence') setConvergence(event); });
  const data = monitor.data;
  const residualFields = useMemo(() => [...new Set((data?.residuals || []).flatMap((r) => Object.keys(r)).filter((k) => k !== 'iteration'))], [data]);
  const forceFields = useMemo(() => [...new Set((data?.forces || []).flatMap((r) => Object.keys(r)).filter((k) => k !== 'iteration'))], [data]);
  if (!data?.forces.length && !data?.residuals.length) return <p className="hint">Charts appear when the solver writes its first iterations.</p>;
  const state = convergence || data?.convergence;
  const start = state?.averaging_start ?? state?.averaging?.start;
  return <div className="space-y-4">
    {state && <Notice tone={state.state === 'converged' ? 'good' : state.state === 'runaway' ? 'bad' : 'active'}>
      {state.message || (state.state === 'averaging' ? `Convergence detected at iteration ${start}; averaging the loads.` : 'Searching for load convergence; the solve stops early once loads settle.')}
    </Notice>}
    <div className="grid gap-4 xl:grid-cols-2">
      <SeriesChart title="Monitored loads" yLabel="N" series={forceFields.map((f) => ({ name: f.replace('_n', '').replace('_', ' '), values: data!.forces.filter((r) => r[f] != null).map((r) => [r.iteration, r[f]] as [number, number]) }))}
        band={start != null ? { start, end: Math.max(...data!.forces.map((r) => r.iteration)), label: 'averaging window' } : null} />
      <SeriesChart title="Residuals" log yLabel="Initial residual" series={residualFields.map((f) => ({ name: f, values: data!.residuals.filter((r) => r[f] != null && r[f] > 0).map((r) => [r.iteration, r[f]] as [number, number]) }))} />
    </div>
  </div>;
}

export function ProgressView({ simulation }: { simulation: Simulation }) {
  const current = useRun(simulation.current_run_id);
  const run = current.data;
  const log = useRunEvents(simulation.current_run_id);
  const mesh = simulation.stages.filter((s) => s.kind === 'mesh').at(-1);
  const meshRun = useRun(simulation.status === 'awaiting_review' ? (mesh?.id || simulation.current_run_id) : null);
  const client = useQueryClient();
  const failed = simulation.status === 'failed';
  const solveRunning = run?.kind === 'solve' && isActive(run.status);
  async function cancel() { try { await post(`/simulations/${simulation.id}/cancel`); client.invalidateQueries({ queryKey: ['simulation'] }); } catch (e) { toast.error(e); } }
  async function retry() {
    try { await post(`/runs/${simulation.current_run_id}/retry`); client.invalidateQueries({ queryKey: ['simulation'] }); toast.info('A new attempt is queued; the failed one is kept.'); }
    catch (e) { toast.error(e); }
  }
  return <div className="space-y-4">
    <Card>
      <div className="space-y-4">
        <StageTimeline simulation={simulation} />
        {run && isActive(run.status) && <ProgressBar value={run.progress?.percent ?? 5} label={run.progress?.label} />}
        {simulation.status === 'queued' && !run?.started && <p className="hint">Waiting for the compute worker. Jobs run one at a time per lane.</p>}
        {failed && <Notice tone="bad" title="This attempt failed">{simulation.error || run?.error || 'See the log for details.'}</Notice>}
        <div className="flex gap-2">
          {isActive(simulation.status) && <button className="btn-secondary btn-sm" onClick={cancel}><Square size={13} />Cancel</button>}
          {failed && <button className="btn-primary btn-sm" onClick={retry}><RotateCcw size={13} />Retry as a new attempt</button>}
        </div>
      </div>
    </Card>
    {simulation.status === 'awaiting_review' && meshRun.data && <MeshReview simulation={simulation} mesh={meshRun.data} />}
    {(solveRunning || run?.kind === 'solve') && <Card title="Solver"><LiveCharts runId={run!.id} live={solveRunning} /></Card>}
    <Card title="Log" actions={run && <span className="hint">{run.kind} · attempt {run.attempt || 1}</span>}>
      <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-lg bg-surface-3 p-3 font-mono text-[11px] leading-relaxed text-ink-2" aria-live="polite">{log.join('\n') || 'No log output yet.'}</pre>
    </Card>
  </div>;
}
