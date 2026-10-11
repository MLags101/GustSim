import { useQueryClient } from '@tanstack/react-query';
import { Download, FileText, RefreshCw } from 'lucide-react';
import { download, post } from '../../api/client';
import { useDocument, useRun } from '../../api/queries';
import type { Manifest, Run, Simulation } from '../../api/types';
import { usePreferences } from '../../state/preferences';
import { formatQuantity, significant } from '../../lib/units';
import { Card, EmptyState, EvidenceStrip, Metric, Notice, toast } from '../../ui';

export function ElementsTab({ run }: { run: Run }) {
  const elements = useDocument<any>(run.id, 'elements');
  const units = usePreferences((s) => s.units);
  const data = elements.data;
  const metrics = run.result?.metrics || {};
  if (!data) return <EmptyState title="No element results">Rotating parts, radiators and openings report here.</EmptyState>;
  return <div className="space-y-4">
    {data.rotating_zones.map((zone: any) => <Card key={zone.name} title={`Rotor · ${zone.name}`}>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="Speed" value={formatQuantity('rotation', zone.rpm, units.rotation)} />
        <Metric label="Thrust" value={formatQuantity('force', zone.thrust_n, units.force)} />
        <Metric label="Shaft torque" value={formatQuantity('moment', zone.torque_nm, units.moment)} help="Required driving torque, positive in the commanded direction." />
        <Metric label="Shaft power" value={formatQuantity('power', zone.shaft_power_w, units.power)} />
        <Metric label="Advance ratio J" value={significant(zone.advance_ratio, 3)} />
        <Metric label="Ct" value={significant(zone.ct, 4)} /><Metric label="Cq" value={significant(zone.cq, 4)} />
        <Metric label="Efficiency η" value={zone.efficiency != null ? `${(zone.efficiency * 100).toFixed(1)} %` : '—'} help={zone.efficiency == null ? 'Propulsive efficiency is undefined at zero advance speed.' : undefined} />
      </div>
    </Card>)}
    {data.openings.length > 0 && <Card title="Inlets, outlets and boundaries">
      <table className="table-clean"><thead><tr><th>Surface</th><th>Condition</th><th className="text-right">Volume flow</th><th className="text-right">Mass flow</th><th className="text-right">Mean pressure</th></tr></thead>
        <tbody>{data.openings.map((o: any) => <tr key={o.patch}><td className="font-medium">{o.patch}</td><td>{o.kind.replaceAll('_', ' ')}</td>
          <td className="text-right tabular-nums">{formatQuantity('flow', o.volume_flow_m3_s, units.flow)}</td><td className="text-right tabular-nums">{formatQuantity('mass_flow', o.mass_flow_kg_s, units.mass_flow)}</td>
          <td className="text-right tabular-nums">{formatQuantity('pressure', o.mean_pressure_pa, units.pressure)}</td></tr>)}</tbody></table>
      <p className="hint mt-2">{data.notes?.flux_sign}.</p>
    </Card>}
    {metrics.pressure_drop_pa != null && <Card title="Internal flow"><div className="grid gap-3 sm:grid-cols-4">
      <Metric label="Static pressure drop" value={formatQuantity('pressure', metrics.pressure_drop_pa, units.pressure)} />
      <Metric label="Total-pressure loss" value={formatQuantity('pressure', metrics.total_pressure_loss_pa, units.pressure)} />
      <Metric label="Head loss" value={significant(metrics.head_loss_m, 4)} unit="m" />
      <Metric label="Outlet mass flow" value={formatQuantity('mass_flow', metrics.outlet_mass_flow_kgs, units.mass_flow)} />
    </div></Card>}
  </div>;
}

const EXPORTS: [string, string, string][] = [
  ['pdf', 'PDF report', 'Forces, convergence, pressure maps, insights and the setup, with evidence states on every page.'],
  ['share', 'Shared report (offline HTML)', 'One file that opens in any browser without GustSim or a network: the 3D results, forces and insights, read-only. Large layers may be left out (about 60 MB budget).'],
  ['report', 'HTML report', 'A single-page report with all findings and the reproducible configuration.'],
  ['package', 'Results package (ZIP)', 'Browser layers (VTP), renders, forces, insights and convergence JSON.'],
  ['bundle', 'Complete bundle (ZIP)', 'The raw OpenFOAM case, logs, HDF5 fields, readers for Python and MATLAB.'],
  ['case', 'OpenFOAM case (ZIP)', 'The case directory as solved, with logs and the manifest.'],
  ['hdf5', 'Fields (HDF5)', 'Volume mesh and cell/point fields with units.'],
  ['vtk', 'Volume (VTU)', 'The internal mesh with fields for ParaView.'],
  ['csv', 'Load history (CSV)', 'Forces and moments per iteration.'],
  ['paraview', 'ParaView state', 'A .pvsm state of the default view.'],
];

export function DownloadsTab({ run, manifest }: { run: Run; manifest: Manifest | null }) {
  const detail = useRun(run.id);
  const available = detail.data?.result?.export_available || {};
  const client = useQueryClient();
  async function regenerate(kind: 'report' | 'package') {
    try { await post(`/runs/${run.id}/${kind}`); toast.info(kind === 'report' ? 'The PDF report is being generated.' : 'The results package is being rebuilt.'); client.invalidateQueries({ queryKey: ['run', run.id] }); }
    catch (e) { toast.error(e); }
  }
  const name = (run.spec.name || 'gustsim').replace(/[^A-Za-z0-9._-]+/g, '-');
  return <div className="grid gap-4 xl:grid-cols-[1.3fr_1fr]">
    <Card title="Downloads">
      <ul className="divide-y divide-line">{EXPORTS.map(([kind, title, text]) => <li key={kind} className="flex items-center gap-3 py-3">
        <FileText size={18} className="shrink-0 text-muted" />
        <div className="min-w-0 flex-1"><div className="text-sm font-semibold">{title}</div><p className="hint">{text}</p></div>
        {available[kind] === false && kind === 'pdf' ? <button className="btn-secondary btn-sm" onClick={() => regenerate('report')}><RefreshCw size={13} />Generate</button>
          : <button className="btn-secondary btn-sm" disabled={available[kind] === false} onClick={() => download(`/runs/${run.id}/export/${kind}`, `${name}-${kind}`).catch(toast.error)}><Download size={13} />Download</button>}
      </li>)}
        <li className="flex items-center gap-3 py-3"><FileText size={18} className="shrink-0 text-muted" /><div className="min-w-0 flex-1"><div className="text-sm font-semibold">Model geometry</div><p className="hint">The exact surface this run used, in metres.</p></div>
          {['stl', 'obj', 'glb'].map((f) => <button key={f} className="btn-ghost btn-sm uppercase" onClick={() => download(`/geometry/${run.spec.geometry_id}/export/${f}`, `${name}.${f}`).catch(toast.error)}>{f}</button>)}</li>
      </ul>
      {!manifest && <div className="mt-3"><Notice tone="warn">This run has no results package. <button className="font-semibold underline" onClick={() => regenerate('package')}>Build it now</button> to add clouds, streamlines, sections, forces by part and insights.</Notice></div>}
    </Card>
    <Card title="Renders">
      {manifest?.renders.length ? <div className="grid grid-cols-2 gap-2">{manifest.renders.map((r) => <a key={r.id} href={`/api/runs/${run.id}/files/${r.url}`} target="_blank" rel="noreferrer" className="group overflow-hidden rounded-lg border border-line">
        <img src={`/api/runs/${run.id}/files/${r.url}`} alt={`${r.view} view, ${r.layer}`} className="aspect-[16/10] w-full bg-white object-cover transition group-hover:scale-[1.02]" loading="lazy" />
        <div className="px-2 py-1 text-xs capitalize text-muted">{r.view} · {r.layer === 'cp' ? 'pressure' : 'loss cloud'}</div></a>)}</div>
        : <p className="hint">Renders are produced with the results package.</p>}
    </Card>
  </div>;
}

export function InfoTab({ simulation, run }: { simulation: Simulation; run: Run }) {
  const spec = run.spec;
  const result = run.result || {};
  return <div className="grid gap-4 xl:grid-cols-2">
    <Card title="Evidence"><EvidenceStrip block={simulation.status_block} />
      <p className="hint mt-3">Execution says the jobs ran; numerical quality summarises mesh, residual, mass-balance and load-convergence checks; experimental validation compares against measurements and stays pending until the benchmark gates for this template pass.</p>
      <ul className="mt-3 space-y-1.5">{(result.findings || []).map((f: any) => <li key={f.code} className="text-sm"><span className="mr-1.5 text-xs font-bold uppercase text-muted">{f.status}</span>{f.detail}</li>)}</ul>
    </Card>
    <Card title="Setup">
      <dl className="grid gap-x-4 gap-y-1 text-sm sm:grid-cols-2">{[
        ['Model', simulation.geometry.filename], ['Case', simulation.setup_summary.use_case], ['Fluid', spec.fluid?.name], ['Speed', `${spec.flow?.speed} m/s`],
        ['Turbulence', spec.flow?.turbulence], ['Accuracy tier', spec.mesh?.tier], ['Cells', result.cell_count?.toLocaleString?.() ?? '—'], ['Processes', spec.solver?.processes],
        ['Termination', result.termination ?? '—'], ['Run', run.id.slice(0, 12)], ['Configuration', (run.configuration_id || '').slice(0, 12)], ['Attempts', simulation.stages.length],
      ].map(([k, v]) => <div key={String(k)} className="flex justify-between gap-2 border-b border-line py-1"><dt className="text-muted">{k}</dt><dd className="font-medium">{String(v ?? '—')}</dd></div>)}</dl>
      <details className="mt-3"><summary className="cursor-pointer text-xs font-semibold text-muted">Configuration snapshot (JSON)</summary><pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-surface-3 p-3 text-[11px]">{JSON.stringify(spec, null, 2)}</pre></details>
    </Card>
    <Card title="Attempts">
      <table className="table-clean"><thead><tr><th>Stage</th><th>Attempt</th><th>Status</th><th>Note</th></tr></thead>
        <tbody>{simulation.stages.map((s) => <tr key={s.id}><td className="capitalize">{s.kind}</td><td>{s.attempt}</td><td>{s.status}</td><td className="text-xs text-muted">{s.retry_reason || s.error || ''}</td></tr>)}</tbody></table>
      <p className="hint mt-2">Retries are new attempts; earlier attempts and their files are kept.</p>
    </Card>
  </div>;
}

export function NoResults({ simulation }: { simulation: Simulation }) {
  return <EmptyState title="No results yet">{simulation.status === 'failed' ? 'The simulation failed before producing results.' : 'Results appear here when the solve completes.'}</EmptyState>;
}
