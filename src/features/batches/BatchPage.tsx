import { Link } from 'wouter';
import { ArrowLeft, Layers } from 'lucide-react';
import { isActive, useBatch } from '../../api/queries';
import type { StudyPath } from '../../api/types';
import { usePreferences } from '../../state/preferences';
import { UNITS, formatQuantity, significant, toDisplay } from '../../lib/units';
import { SeriesChart } from '../../charts/SeriesChart';
import { Card, EmptyState, ExperimentalBadge, Spinner } from '../../ui';
import { StatusBadge } from '../dashboard/DashboardPage';
import { PARAMETERS } from './BatchDialog';

function axisLabel(path: StudyPath, units: Record<string, string>) {
  const parameter = PARAMETERS.find((p) => p.path === path);
  if (!parameter) return path;
  const unit = parameter.quantity ? UNITS[parameter.quantity][units[parameter.quantity]]?.label : parameter.unit;
  return unit ? `${parameter.label} (${unit})` : parameter.label;
}

function displayValue(path: StudyPath, value: number | string, units: Record<string, string>) {
  const parameter = PARAMETERS.find((p) => p.path === path);
  if (typeof value !== 'number' || !parameter?.quantity) return String(value);
  return significant(toDisplay(parameter.quantity, value, units[parameter.quantity]), 4);
}

export default function BatchPage({ id }: { id: string }) {
  const batch = useBatch(id);
  const units = usePreferences((s) => s.units);
  if (batch.isError) return <EmptyState title="Batch not found"><Link href="/" className="btn-primary mt-2">Back to simulations</Link></EmptyState>;
  const data = batch.data;
  if (!data) return <div className="grid flex-1 place-items-center"><Spinner label="Loading batch" /></div>;
  const parameters = data.definition.parameters || [];
  const active = data.members.filter((m) => isActive(m.status) || m.status === 'awaiting_review').length;
  const done = data.members.filter((m) => m.status === 'completed').length;
  const numericX = parameters[0] && parameters[0].path !== 'mesh.tier';
  const cd = data.series.find((s) => s.y === 'cd') || data.series.find((s) => s.y === 'drag_n');
  return <div className="mx-auto flex w-full max-w-[1400px] flex-1 flex-col gap-4 px-4 py-4">
    <header className="flex flex-wrap items-start gap-3">
      <Link href="/" className="btn-ghost btn-icon mt-0.5" aria-label="Back to simulations"><ArrowLeft size={18} /></Link>
      <div className="min-w-0 flex-1 space-y-1">
        <h1 className="flex items-center gap-2 text-xl font-semibold"><Layers size={20} className="text-muted" />{data.name}</h1>
        <p className="text-sm text-muted">{data.definition.kind === 'sweep' ? 'Sweep' : 'Grid'} of {parameters.map((p) => axisLabel(p.path, units)).join(' × ')} ·
          {' '}{data.members.length} simulations · {done} completed{active ? ` · ${active} in progress` : ''}</p>
        {data.shared_meshes.length > 0 && <p className="hint">{data.members.filter((m) => m.batch?.mesh_from).length} members reuse a mesh built by another member, so the mesh is built and checked once.</p>}
      </div>
    </header>
    {cd && numericX && cd.points.length > 1 && <Card title={cd.y === 'cd' ? 'Drag coefficient' : 'Drag'}>
      <SeriesChart xLabel={axisLabel(cd.x, units)} yLabel={cd.y === 'cd' ? 'Cd' : UNITS.force[units.force]?.label || 'N'} height={220}
        series={[{ name: cd.y === 'cd' ? 'Cd' : 'drag', values: cd.points.map((p) => [Number(displayValue(cd.x, p.x, units)),
          cd.y === 'cd' ? p.y : toDisplay('force', p.y, units.force)] as [number, number]) }]} />
      <p className="hint">Each point is one simulation's averaged result; a line only joins completed members and does not interpolate physics.</p>
    </Card>}
    <Card title="Members" pad={false}>
      <div className="overflow-x-auto"><table className="w-full text-sm">
        <thead><tr className="border-b border-line text-left text-xs uppercase tracking-wide text-muted">
          {parameters.map((p) => <th key={p.path} className="px-4 py-2 font-semibold">{axisLabel(p.path, units)}</th>)}
          <th className="px-4 py-2 font-semibold">Status</th><th className="px-4 py-2 font-semibold">Cd</th><th className="px-4 py-2 font-semibold">Drag</th>
          <th className="px-4 py-2 font-semibold">Mesh</th><th className="px-4 py-2" /></tr></thead>
        <tbody>{data.members.map((m) => <tr key={m.id} className="border-b border-line last:border-b-0">
          {parameters.map((p) => <td key={p.path} className="px-4 py-2.5 tabular-nums">{m.batch?.values?.[p.path] != null ? displayValue(p.path, m.batch.values[p.path], units) : '—'}</td>)}
          <td className="px-4 py-2.5"><span className="inline-flex items-center gap-1.5"><StatusBadge status={m.status} />{m.experimental.length > 0 && <ExperimentalBadge />}</span></td>
          <td className="px-4 py-2.5 tabular-nums">{m.headline.cd != null ? significant(m.headline.cd, 4) : '—'}</td>
          <td className="px-4 py-2.5 tabular-nums">{formatQuantity('force', m.headline.drag_n ?? null, units.force)}</td>
          <td className="px-4 py-2.5 text-muted">{m.batch?.mesh_from ? 'shared' : 'own'}</td>
          <td className="px-4 py-2.5 text-right"><Link href={`/simulations/${m.id}`} className="btn-ghost btn-sm">Open</Link></td>
        </tr>)}</tbody>
      </table></div>
    </Card>
    <p className="hint">Every member keeps its own snapshot, evidence and validation status; experimental validation is pending for all of them.</p>
  </div>;
}
