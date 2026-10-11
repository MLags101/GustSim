import { useEffect, useMemo, useState } from 'react';
import { Link, useLocation } from 'wouter';
import { useQueryClient } from '@tanstack/react-query';
import { Archive, ArchiveRestore, FileClock, GitCompareArrows, Layers, LayoutGrid, Plus, Rows3, Search, Tag, Wind } from 'lucide-react';
import { patch } from '../../api/client';
import { isActive, useBatches, useProjects, useSimulations } from '../../api/queries';
import type { Simulation } from '../../api/types';
import { usePreferences } from '../../state/preferences';
import { formatQuantity, significant } from '../../lib/units';
import { evidence, toneClass } from '../../lib/evidence';
import { Badge, Card, Dialog, EmptyState, ExperimentalBadge, Segmented, Spinner, cx, toast } from '../../ui';
import { NewSimulationDialog } from './NewSimulationDialog';
import { startTour } from '../onboarding/tour';

const STATUS: Record<string, [string, 'good' | 'warn' | 'bad' | 'neutral' | 'active']> = {
  queued: ['Queued', 'active'], meshing: ['Meshing', 'active'], awaiting_review: ['Mesh review', 'warn'], solving: ['Solving', 'active'],
  postprocessing: ['Post-processing', 'active'], completed: ['Completed', 'good'], failed: ['Failed', 'bad'], cancelled: ['Cancelled', 'neutral'],
};

export function StatusBadge({ status }: { status: string }) {
  const [label, tone] = STATUS[status] || [status, 'neutral'];
  return <Badge tone={tone}>{isActive(status) && <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-current" />}{label}</Badge>;
}

const USE_CASE: Record<string, string> = { aerodynamics: 'External aero', propeller: 'Rotor', pipe: 'Internal flow', custom: 'Custom' };

export function KeyMetric({ simulation }: { simulation: Simulation }) {
  const units = usePreferences((s) => s.units);
  const h = simulation.headline || {};
  const kind = simulation.setup_summary.use_case;
  if (kind === 'propeller' && h.thrust_n != null) return <span>{formatQuantity('force', h.thrust_n, units.force)} <span className="text-muted">thrust</span></span>;
  if (kind === 'pipe' && h.pressure_drop_pa != null) return <span>{formatQuantity('pressure', h.pressure_drop_pa, units.pressure)} <span className="text-muted">Δp</span></span>;
  if (h.cd != null) return <span>Cd {significant(h.cd, 3)}</span>;
  if (h.drag_n != null) return <span>{formatQuantity('force', h.drag_n, units.force)} <span className="text-muted">drag</span></span>;
  return <span className="text-muted">—</span>;
}

function Thumbnail({ simulation, className }: { simulation: Simulation; className?: string }) {
  return simulation.thumbnail_url
    ? <img src={simulation.thumbnail_url} alt="" className={cx('rounded-lg border border-line bg-white object-cover', className)} loading="lazy" />
    : <div className={cx('grid place-items-center rounded-lg border border-line bg-surface-3 text-muted', className)}><Wind size={18} /></div>;
}

function relative(time: number) {
  const seconds = Date.now() / 1000 - time;
  if (seconds < 60) return 'just now';
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} h ago`;
  return new Date(time * 1000).toLocaleDateString();
}

export function DashboardPage() {
  const [archived, setArchived] = useState(false);
  const simulations = useSimulations(archived);
  const projects = useProjects();
  const batches = useBatches();
  const { viewMode, setViewMode } = usePreferences();
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState('all');
  const [kind, setKind] = useState('all');
  const [tag, setTag] = useState('');
  const [selected, setSelected] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  useEffect(() => { const timer = window.setTimeout(() => startTour(false), 600); return () => window.clearTimeout(timer); }, []);
  const [tagging, setTagging] = useState(false);
  const [newTag, setNewTag] = useState('');
  const client = useQueryClient();
  const [, navigate] = useLocation();

  const all = simulations.data || [];
  const tags = useMemo(() => [...new Set(all.flatMap((s) => s.tags))].sort(), [all]);
  const rows = all.filter((s) => (!query || s.name.toLowerCase().includes(query.toLowerCase()) || s.geometry.filename?.toLowerCase().includes(query.toLowerCase()))
    && (status === 'all' || (status === 'active' ? isActive(s.status) || s.status === 'awaiting_review' : s.status === status))
    && (kind === 'all' || s.setup_summary.use_case === kind) && (!tag || s.tags.includes(tag)));
  const launchedProjects = new Set(all.map((s) => s.project_id));
  const drafts = (projects.data || []).filter((p) => !launchedProjects.has(p.id)).slice(0, 6);
  const chosen = rows.filter((s) => selected.includes(s.id));
  const comparable = chosen.filter((s) => s.results_run_id);

  async function bulk(update: (s: Simulation) => Record<string, unknown>, message: string) {
    try {
      await Promise.all(chosen.map((s) => patch(`/simulations/${s.id}`, update(s))));
      setSelected([]);
      client.invalidateQueries({ queryKey: ['simulations'] });
      toast.success(message);
    } catch (e) { toast.error(e); }
  }
  const toggle = (id: string) => setSelected((v) => (v.includes(id) ? v.filter((x) => x !== id) : [...v, id]));

  return <div className="flex-1">
    <section className="hero text-white">
      <div className="mx-auto flex max-w-[1800px] flex-col gap-4 px-4 py-8 sm:flex-row sm:items-end sm:justify-between sm:py-10">
        <div>
          <p className="text-sm font-medium text-cyan-200/80">Local aerodynamic simulation · OpenFOAM v2606</p>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight text-white sm:text-4xl">Simulations</h1>
          <p className="mt-2 max-w-xl text-sm text-slate-300">Upload a model, set the flow in a few steps, and get forces, pressure maps, streamlines and a report. Everything runs on this computer.</p>
        </div>
        <button className="btn-primary self-start px-5 py-2.5 text-base sm:self-auto" onClick={() => setCreating(true)} data-tour="new-simulation"><Plus size={18} />New simulation</button>
      </div>
    </section>

    <div className="mx-auto max-w-[1800px] space-y-6 px-4 py-6">
      {drafts.length > 0 && <section>
        <h2 className="eyebrow mb-2">Setups in progress</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {drafts.map((p) => <Link key={p.id} href={`/setup/${p.id}`} className="card flex items-center gap-3 px-3.5 py-3 transition hover:border-accent">
            <FileClock size={18} className="shrink-0 text-muted" /><span className="min-w-0"><span className="block truncate text-sm font-semibold">{p.name}</span><span className="hint">Edited {relative(p.updated)}</span></span>
          </Link>)}
        </div>
      </section>}

      {(batches.data || []).length > 0 && <section>
        <h2 className="eyebrow mb-2">Batches</h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {(batches.data || []).slice(0, 6).map((b) => <Link key={b.id} href={`/batches/${b.id}`} className="card flex items-center gap-3 px-3.5 py-3 transition hover:border-accent">
            <Layers size={18} className="shrink-0 text-muted" /><span className="min-w-0"><span className="block truncate text-sm font-semibold">{b.name}</span><span className="hint">Created {relative(b.created)}</span></span>
          </Link>)}
        </div>
      </section>}

      <Card pad={false}>
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3">
          <div className="relative min-w-[14rem] flex-1 sm:max-w-xs">
            <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
            <input className="input pl-9" placeholder="Search simulations or models" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search simulations" />
          </div>
          <select className="input w-auto" aria-label="Filter by status" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="all">All statuses</option><option value="active">Running</option><option value="completed">Completed</option><option value="awaiting_review">Mesh review</option><option value="failed">Failed</option>
          </select>
          <select className="input w-auto" aria-label="Filter by flow type" value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="all">All flow types</option><option value="aerodynamics">External aero</option><option value="propeller">Rotor</option><option value="pipe">Internal flow</option>
          </select>
          {tags.length > 0 && <select className="input w-auto" aria-label="Filter by tag" value={tag} onChange={(e) => setTag(e.target.value)}>
            <option value="">All tags</option>{tags.map((t) => <option key={t} value={t}>{t}</option>)}
          </select>}
          <div className="ml-auto flex items-center gap-2">
            <button className={cx('btn-ghost btn-sm', archived && 'bg-surface-3')} onClick={() => { setArchived(!archived); setSelected([]); }}><Archive size={15} />{archived ? 'Archived' : 'Show archived'}</button>
            <Segmented size="sm" label="View" value={viewMode} onChange={setViewMode} options={[{ value: 'table', label: <Rows3 size={15} aria-label="Table" /> }, { value: 'cards', label: <LayoutGrid size={15} aria-label="Cards" /> }]} />
          </div>
        </div>
        {selected.length > 0 && <div className="flex flex-wrap items-center gap-2 border-b border-line bg-accent-soft px-4 py-2 text-sm">
          <span className="font-semibold text-accent-ink">{selected.length} selected</span>
          <button className="btn-secondary btn-sm" disabled={comparable.length < 2} onClick={() => navigate(`/compare?runs=${comparable.map((s) => s.results_run_id).join(',')}`)} title={comparable.length < 2 ? 'Select at least two completed simulations' : undefined}><GitCompareArrows size={14} />Compare</button>
          <button className="btn-secondary btn-sm" onClick={() => setTagging(true)}><Tag size={14} />Add tag</button>
          <button className="btn-secondary btn-sm" onClick={() => bulk(() => ({ archived: !archived }), archived ? 'Restored' : 'Archived')}>{archived ? <ArchiveRestore size={14} /> : <Archive size={14} />}{archived ? 'Restore' : 'Archive'}</button>
          <button className="btn-ghost btn-sm ml-auto" onClick={() => setSelected([])}>Clear</button>
        </div>}
        {simulations.isLoading ? <div className="p-10 text-center"><Spinner /></div>
          : rows.length === 0 ? <EmptyState icon={<Wind size={22} />} title={all.length ? 'No simulations match these filters' : 'Your next simulation starts here'}
            actions={!all.length && <button className="btn-primary" onClick={() => setCreating(true)}><Plus size={16} />New simulation</button>}>
            {!all.length && 'Import a STEP, IGES, STL, OBJ or glTF model, or try a sample. The model never leaves this computer.'}
          </EmptyState>
          : viewMode === 'table' ? <div className="overflow-x-auto">
            <table className="table-clean min-w-[56rem]">
              <thead><tr>
                <th className="w-10"><input type="checkbox" aria-label="Select all" checked={selected.length === rows.length} onChange={(e) => setSelected(e.target.checked ? rows.map((r) => r.id) : [])} /></th>
                <th>Simulation</th><th>Case</th><th>Accuracy</th><th>Status</th><th>Result</th><th>Evidence</th><th>Updated</th>
              </tr></thead>
              <tbody>{rows.map((s) => <tr key={s.id} className="cursor-pointer hover:bg-surface-2" onClick={() => navigate(`/simulations/${s.id}`)}>
                <td onClick={(e) => e.stopPropagation()}><input type="checkbox" aria-label={`Select ${s.name}`} checked={selected.includes(s.id)} onChange={() => toggle(s.id)} /></td>
                <td><div className="flex items-center gap-3"><Thumbnail simulation={s} className="h-10 w-16 shrink-0" />
                  <div className="min-w-0"><div className="truncate font-semibold text-ink">{s.name}</div>
                    <div className="mt-0.5 flex flex-wrap gap-1">{s.experimental.length > 0 && <ExperimentalBadge />}{s.tags.map((t) => <span key={t} className="chip">{t}</span>)}</div></div></div></td>
                <td><div>{USE_CASE[s.setup_summary.use_case] || s.setup_summary.use_case}</div><div className="hint">{s.setup_summary.fluid} · {formatQuantity('speed', s.setup_summary.speed_m_s, usePreferences.getState().units.speed, 3)}</div></td>
                <td className="capitalize">{s.setup_summary.tier}</td>
                <td><StatusBadge status={s.status} /></td>
                <td className="tabular-nums"><KeyMetric simulation={s} /></td>
                <td><div className="flex gap-1">{evidence(s.status_block).filter((e) => ['numerical', 'validation'].includes(e.key)).map((e) => <span key={e.key} title={e.help} className={cx('rounded-md px-1.5 py-0.5 text-[11px] font-semibold', toneClass[e.tone])}>{e.key === 'numerical' ? 'Num' : 'Exp'}: {e.value}</span>)}</div></td>
                <td className="whitespace-nowrap text-muted">{relative(s.updated)}</td>
              </tr>)}</tbody>
            </table>
          </div>
          : <div className="grid gap-4 p-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
            {rows.map((s) => <div key={s.id} className="card overflow-hidden transition hover:border-accent">
              <Link href={`/simulations/${s.id}`} className="block"><Thumbnail simulation={s} className="aspect-[16/10] w-full rounded-none border-0 border-b" /></Link>
              <div className="space-y-2 p-3.5">
                <div className="flex items-start gap-2"><input type="checkbox" className="mt-1" aria-label={`Select ${s.name}`} checked={selected.includes(s.id)} onChange={() => toggle(s.id)} />
                  <Link href={`/simulations/${s.id}`} className="min-w-0 flex-1 truncate font-semibold">{s.name}</Link><StatusBadge status={s.status} /></div>
                <div className="flex items-center justify-between text-sm"><span className="text-muted">{USE_CASE[s.setup_summary.use_case]}</span><span className="font-semibold tabular-nums"><KeyMetric simulation={s} /></span></div>
                <div className="flex flex-wrap gap-1">{s.experimental.length > 0 && <ExperimentalBadge />}{s.tags.map((t) => <span key={t} className="chip">{t}</span>)}</div>
              </div>
            </div>)}
          </div>}
      </Card>
      <p className="hint">Completed runs report numerical checks; experimental validation stays pending until GustSim's benchmark gates pass for each template.</p>
    </div>
    <NewSimulationDialog open={creating} onOpenChange={setCreating} />
    <Dialog open={tagging} onOpenChange={setTagging} title="Add a tag" description={`Applies to ${chosen.length} simulation${chosen.length === 1 ? '' : 's'}.`}>
      <form onSubmit={(e) => { e.preventDefault(); const t = newTag.trim(); if (!t) return; bulk((s) => ({ tags: [...new Set([...s.tags, t])] }), `Tagged “${t}”`); setTagging(false); setNewTag(''); }} className="flex gap-2">
        <input className="input" autoFocus placeholder="e.g. baseline, concept-b" value={newTag} onChange={(e) => setNewTag(e.target.value)} maxLength={40} list="known-tags" />
        <datalist id="known-tags">{tags.map((t) => <option key={t} value={t} />)}</datalist>
        <button className="btn-primary" type="submit">Add</button>
      </form>
    </Dialog>
  </div>;
}
