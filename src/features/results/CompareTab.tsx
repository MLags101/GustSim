import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { post } from '../../api/client';
import { useCompare, useManifest, useSimulations } from '../../api/queries';
import type { CameraPreset, CompareResult, Manifest, Run } from '../../api/types';
import { significant } from '../../lib/units';
import { Card, EmptyState, EvidenceStrip, Notice, Segmented, cx, toast } from '../../ui';
import { Legend } from '../../viewer/Legend';
import { ViewerFrame } from '../../viewer/ViewerFrame';

export function CompareTable({ data }: { data: CompareResult }) {
  return <div className="overflow-x-auto"><table className="table-clean min-w-[36rem]">
    <thead><tr><th>Quantity</th>{data.rows.map((r) => <th key={r.run_id} className="text-right">{r.name}{r.run_id === data.baseline && ' (baseline)'}</th>)}</tr></thead>
    <tbody>{data.columns.map((c) => <tr key={c.id}><td className="font-medium">{c.label}{c.unit !== '1' && <span className="text-muted"> · {c.unit}</span>}</td>
      {data.rows.map((r) => { const d = r.deltas[c.id]; return <td key={r.run_id} className="text-right tabular-nums">
        {significant(r.values[c.id], 4)}
        {r.run_id !== data.baseline && d && <span className={cx('ml-1.5 text-xs', d.rel == null ? 'text-muted' : Math.abs(d.rel) < 0.005 ? 'text-muted' : 'text-accent-ink')}>{d.rel != null ? `${d.rel > 0 ? '+' : ''}${(d.rel * 100).toFixed(1)} %` : `${d.abs > 0 ? '+' : ''}${significant(d.abs, 3)}`}</span>}
      </td>; })}</tr>)}</tbody>
  </table></div>;
}

export function CompareTab({ run, manifest }: { run: Run; manifest: Manifest | null }) {
  const simulations = useSimulations();
  const candidates = (simulations.data || []).filter((s) => s.results_run_id && s.results_run_id !== run.id);
  const [other, setOther] = useState<string>('');
  const [mode, setMode] = useState<'side' | 'delta'>('side');
  const [camera, setCamera] = useState<CameraPreset | null>(null);
  const otherManifest = useManifest(other || null);
  const table = useCompare(other ? [run.id, other] : []);
  const delta = useMutation({ mutationFn: () => post<{ url: string; delta_range: [number, number] | null; coverage: number; note: string }>('/compare/surface', { a: run.id, b: other, field: 'Cp' }), onError: (e) => toast.error(e) });
  if (!candidates.length) return <EmptyState title="Nothing to compare yet">Run another simulation (a design variant, another speed or accuracy) to compare it with this one.</EmptyState>;
  const surfaceUrl = (id: string, m?: Manifest | null) => m?.layers.find((l) => l.id === 'surface')?.url ? `/api/runs/${id}/files/${m!.layers.find((l) => l.id === 'surface')!.url}` : `/api/runs/${id}/files/results/surface.vtp`;
  const otherReady = !!otherManifest.data || otherManifest.isError;
  const field = (m?: Manifest | null) => (m?.layers.find((l) => l.id === 'surface')?.arrays.some((a) => a.name === 'Cp') ? 'Cp' : 'pressure_pa');
  const otherSim = candidates.find((s) => s.results_run_id === other);
  const range: [number, number] = field(manifest) === 'Cp' ? [-1, 1] : [-100, 100];
  const deltaRange: [number, number] = delta.data?.delta_range ? [-Math.max(...delta.data.delta_range.map(Math.abs)), Math.max(...delta.data.delta_range.map(Math.abs))] : [-0.5, 0.5];
  return <div className="space-y-4">
    <div className="flex flex-wrap items-center gap-3">
      <select className="input w-auto min-w-64" aria-label="Compare with" value={other} onChange={(e) => { setOther(e.target.value); delta.reset(); setMode('side'); }}>
        <option value="">Compare with…</option>{candidates.map((s) => <option key={s.id} value={s.results_run_id!}>{s.name}</option>)}
      </select>
      {other && <Segmented size="sm" label="Comparison view" value={mode} onChange={(m) => { setMode(m); if (m === 'delta' && !delta.data) delta.mutate(); }} options={[{ value: 'side', label: 'Side by side' }, { value: 'delta', label: 'Difference map' }]} />}
    </div>
    {other && <>
      {mode === 'side' ? <div className="grid gap-3 lg:grid-cols-2">
        {[{ id: run.id, m: manifest, name: run.spec.name, label: 'A' }, { id: other, m: otherManifest.data, name: otherSim?.name, label: 'B' }].map((pane) => (pane.id === other && !otherReady) ? <div key={pane.id} className="h-[50vh] animate-pulse rounded-2xl border border-line bg-surface-3" /> : <div key={pane.id} className="relative">
          <ViewerFrame className="h-[50vh] rounded-2xl border border-line" bounds={pane.m?.bounds.model} presets={pane.m?.camera_presets}
            layers={[{ id: 'surface', url: surfaceUrl(pane.id, pane.m), field: field(pane.m), colormap: 'cool_to_warm', range, pickable: false }]} onCameraChange={setCamera} syncCamera={camera}>
            <div className="pointer-events-none absolute bottom-3 left-28 rounded-lg bg-surface/90 px-2.5 py-1 text-xs font-semibold shadow-sm"><span className="mr-1.5 rounded bg-indigo-brand px-1.5 text-white">{pane.label}</span>{pane.name}</div>
          </ViewerFrame>
        </div>)}
        <div className="flex justify-end lg:col-span-2"><Legend colormap="cool_to_warm" range={range} label="Pressure (Cp), shared range" unit="1" /></div>
      </div> : <div className="relative">
        {delta.data ? <ViewerFrame className="h-[55vh] rounded-2xl border border-line" bounds={manifest?.bounds.model} presets={manifest?.camera_presets}
          layers={[{ id: 'delta', url: delta.data.url, field: 'delta', colormap: 'advice', range: deltaRange }]}>
          <div className="pointer-events-none absolute bottom-3 right-3"><Legend colormap="advice" range={deltaRange} label="ΔCp (B − A) on A's surface" unit="1" captions={['B lower', 'B higher']} /></div>
        </ViewerFrame> : <div className="h-[55vh] animate-pulse rounded-2xl border border-line bg-surface-3" />}
        {delta.data && <p className="hint mt-2">{delta.data.note} Coverage {(delta.data.coverage * 100).toFixed(1)} %.</p>}
      </div>}
      {table.data && <Card title="Differences">
        {table.data.warnings.map((w) => <div key={w.code} className="mb-2"><Notice tone="warn">{w.detail}</Notice></div>)}
        <CompareTable data={table.data} />
        <div className="mt-3 grid gap-2 sm:grid-cols-2">{table.data.rows.map((r) => <div key={r.run_id}><div className="mb-1 text-xs font-semibold">{r.name}</div><EvidenceStrip compact block={{ execution: r.status.execution, numerical: r.status.numerical, validation: r.status.validation, geometry: r.fidelity }} /></div>)}</div>
        {table.data.config_diff.length > 0 && <details className="mt-3"><summary className="cursor-pointer text-sm font-semibold">What differs in the setup ({table.data.config_diff.length})</summary>
          <table className="table-clean mt-2 text-xs"><tbody>{table.data.config_diff.slice(0, 60).map((d) => <tr key={d.path}><td className="font-mono">{d.path}</td>{d.values.map((v, i) => <td key={i}>{JSON.stringify(v)}</td>)}</tr>)}</tbody></table></details>}
        <p className="hint mt-3">{table.data.note}</p>
      </Card>}
    </>}
  </div>;
}
