import { useEffect, useRef, useState, type ComponentType } from 'react';
import { Link, useLocation } from 'wouter';
import * as Tabs from '@radix-ui/react-tabs';
import { useQueryClient } from '@tanstack/react-query';
import { Activity, ArrowLeft, BarChart3, Boxes, Copy, Download, GitCompareArrows, Info, Lightbulb, Pencil, Spline, Wind } from 'lucide-react';
import { api, patch, post } from '../../api/client';
import { qk, useDocument, useForces, useInsights, useManifest, useRun, useSimulation } from '../../api/queries';
import { EmptyState, EvidenceStrip, ExperimentalBadge, Spinner, Tip, cx, toast } from '../../ui';
import { StatusBadge } from '../dashboard/DashboardPage';
import { ProgressView, StageTimeline } from './ProgressView';
import { FlowTab } from '../results/FlowTab';
import { ForcesTab } from '../results/ForcesTab';
import { InsightsTab } from '../results/InsightsTab';
import { CompareTab } from '../results/CompareTab';
import { DownloadsTab, ElementsTab, InfoTab, hasElements } from '../results/OtherTabs';
import { DesignTab } from '../results/DesignTab';
import { isShare } from '../../share/bundle';

const USE_CASE_LABEL: Record<string, string> = { aerodynamics: 'External aerodynamics', propeller: 'Rotor', pipe: 'Internal flow', custom: 'Custom' };
// Results sections in AirShaper's order. The short label sits under the rail icon; the full
// name is the accessible name and the tooltip.
const ALL_TABS: [string, string, string, ComponentType<{ size?: number; strokeWidth?: number }>][] = [
  ['flow', 'Flow', 'Flow', Wind], ['forces', 'Forces', 'Forces', BarChart3], ['elements', 'Elements', 'Elements', Boxes],
  ['insights', 'Insights', 'Insights', Lightbulb], ['design', 'Design', 'Design advice', Spline], ['compare', 'Compare', 'Compare', GitCompareArrows],
  ['downloads', 'Report', 'Report & downloads', Download], ['info', 'Info', 'Info', Info], ['progress', 'Run', 'Run', Activity],
];
// A shared report is read-only and offline: no comparisons, downloads or live run view.
const SHARE_HIDDEN = ['compare', 'downloads', 'progress'];
// Sections that are a full-bleed 3D view; the others scroll.
const FULL_BLEED = ['flow', 'insights'];

export default function SimulationPage({ id, tab }: { id: string; tab?: string }) {
  const simulation = useSimulation(id);
  const sim = simulation.data;
  const resultsId = sim?.results_run_id || null;
  const run = useRun(resultsId);
  const packaged = !!run.data?.result?.package;
  const manifest = useManifest(resultsId, packaged);
  const forces = useForces(packaged ? resultsId : null);
  const insights = useInsights(packaged ? resultsId : null);
  const elements = useDocument<any>(resultsId, 'elements', packaged);
  const [, navigate] = useLocation();
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);
  // The results package is written onto the solve run by a later job; the run itself is
  // already complete, so refresh it (and its documents) whenever the simulation advances.
  const progress = sim ? `${resultsId}/${sim.status}/${sim.stage}` : null;
  const seen = useRef<string | null>(null);
  useEffect(() => {
    if (!progress) return;
    if (seen.current !== null && seen.current !== progress && resultsId) {
      client.invalidateQueries({ queryKey: qk.run(resultsId) });
      client.invalidateQueries({ queryKey: ['document', resultsId] });
    }
    seen.current = progress;
  }, [client, progress, resultsId]);

  if (simulation.isError) return <EmptyState title="Simulation not found"><Link href="/" className="btn-primary mt-2">Back to simulations</Link></EmptyState>;
  if (!sim || (resultsId && !run.data && run.isFetching)) return <div className="grid flex-1 place-items-center"><Spinner label="Loading simulation" /></div>;
  const hasResults = !!resultsId && !!run.data;
  const showDesign = !!sim.setup_summary.design || !!run.data?.spec?.design?.enabled;
  const showElements = hasElements(elements.data, run.data);
  const TABS = ALL_TABS.filter(([key]) => (showDesign || key !== 'design') && (showElements || key !== 'elements') && !(isShare && SHARE_HIDDEN.includes(key)));
  const active = tab && TABS.some(([k]) => k === tab) ? tab : hasResults ? 'flow' : 'progress';
  const legacy = hasResults && !packaged && sim.status === 'completed';

  async function duplicate() {
    try {
      // Start from the original draft's intent when it exists, else from the model alone.
      const source = sim!.project_id ? await api<{ workspace?: any }>(`/projects/${sim!.project_id}`).catch(() => null) : null;
      const workspace = source?.workspace?.geometry_id === sim!.geometry.id
        ? { version: 2, geometry_id: sim!.geometry.id, intent: source.workspace.intent, overrides: source.workspace.overrides }
        : { version: 2, geometry_id: sim!.geometry.id };
      const project = await post<{ id: string }>('/projects', { name: `${sim!.name} (variant)`, workspace });
      client.invalidateQueries({ queryKey: qk.projects });
      toast.info('A new draft starts from the same model; adjust it and launch a variant.');
      navigate(`/setup/${project.id}`);
    } catch (e) { toast.error(e); }
  }
  async function rename(name: string) {
    setEditing(false);
    if (!name.trim() || name === sim!.name) return;
    try { await patch(`/simulations/${sim!.id}`, { name }); client.invalidateQueries({ queryKey: ['simulation', sim!.id] }); } catch (e) { toast.error(e); }
  }

  const header = <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-line bg-surface px-3 py-2 sm:px-4">
    {!isShare && <Link href="/" className="btn-ghost btn-icon" aria-label="Back to simulations"><ArrowLeft size={18} /></Link>}
    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        {editing ? <input autoFocus className="input max-w-md py-1 font-semibold" defaultValue={sim.name} onBlur={(e) => rename(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') rename((e.target as HTMLInputElement).value); if (e.key === 'Escape') setEditing(false); }} />
          : <h1 className="truncate text-base font-semibold">{sim.name}</h1>}
        {!editing && !isShare && <button className="btn-ghost btn-icon p-1" aria-label="Rename" onClick={() => setEditing(true)}><Pencil size={13} /></button>}
        <StatusBadge status={sim.status} />{sim.experimental.length > 0 && <ExperimentalBadge />}
        {sim.tags.map((t) => <span key={t} className="chip">{t}</span>)}
      </div>
      <div className="flex flex-wrap items-center gap-x-3 text-xs text-muted">
        <span>{sim.geometry.filename}</span><span>{USE_CASE_LABEL[sim.setup_summary.use_case] || sim.setup_summary.use_case} · {sim.setup_summary.fluid} · {sim.setup_summary.speed_m_s} m/s</span>
        <span>{sim.setup_summary.tier[0].toUpperCase() + sim.setup_summary.tier.slice(1)} accuracy</span>{sim.cells && <span>{sim.cells.toLocaleString()} cells</span>}{sim.convergence && <span>{sim.convergence}</span>}
      </div>
    </div>
    <EvidenceStrip block={sim.status_block} compact />
    {!isShare && <button className="btn-secondary btn-sm" onClick={duplicate}><Copy size={14} />Duplicate & edit</button>}
  </header>;

  if (!hasResults && sim.status !== 'completed') return <div className="flex min-h-0 flex-1 flex-col">
    {header}
    <div className="mx-auto w-full max-w-[1500px] flex-1 p-4"><ProgressView simulation={sim} /></div>
  </div>;

  return <div className="flex h-[calc(100dvh-3.5rem)] min-h-[32rem] flex-col">
    {header}
    <Tabs.Root value={active} onValueChange={(value) => navigate(`/simulations/${id}/${value}`, { replace: true })} orientation="vertical" className="flex min-h-0 flex-1">
      <Tabs.List className="flex w-[4.25rem] shrink-0 flex-col items-stretch gap-0.5 overflow-y-auto border-r border-line bg-surface px-1.5 py-2 scrollbar-thin" aria-label="Results" data-tour="results-tabs">
        {TABS.map(([key, short, label, Icon]) => <Tip key={key} content={label}>
          <Tabs.Trigger value={key} aria-label={label} className={cx('relative flex w-14 flex-col items-center gap-0.5 rounded-lg px-1 py-1.5 text-[10px] font-semibold transition-colors',
            active === key ? 'bg-accent-soft text-accent-ink' : 'text-muted hover:bg-surface-3 hover:text-ink')}>
            <Icon size={19} strokeWidth={1.75} /><span className="truncate">{short}</span>
            {key === 'insights' && insights.data?.insights.length ? <span className="absolute right-1 top-0.5 rounded-full bg-accent px-1 text-[9px] leading-4 text-white dark:text-slate-950">{insights.data.insights.length}</span> : null}
          </Tabs.Trigger>
        </Tip>)}
      </Tabs.List>
      <div className="relative min-w-0 flex-1 bg-bg">
        {sim.status !== 'completed' && <div className="absolute inset-x-0 top-0 z-30 flex flex-wrap items-center gap-3 border-b border-line bg-surface/95 px-4 py-2 backdrop-blur"><StageTimeline simulation={sim} /><span className="hint">{sim.stage === 'mesh' || sim.stage === 'review' ? 'A finer mesh is being built from the last solution.' : 'The results package and report are still being built.'}</span></div>}
        {TABS.map(([key]) => <Tabs.Content key={key} value={key} className={cx('absolute inset-0 outline-none', !FULL_BLEED.includes(key) && 'overflow-y-auto scrollbar-thin')}>
          {FULL_BLEED.includes(key) ? section(key) : <div className="mx-auto w-full max-w-[1400px] p-4 sm:p-5">{section(key)}</div>}
        </Tabs.Content>)}
      </div>
    </Tabs.Root>
  </div>;

  function section(key: string) {
    const r = run.data;
    switch (key) {
      case 'flow': return r && <FlowTab run={r} manifest={manifest.data || null} legacy={legacy} forces={forces.data || null} />;
      case 'forces': return r && <ForcesTab run={r} forces={forces.data || null} />;
      case 'elements': return r && <ElementsTab run={r} data={elements.data} />;
      case 'insights': return r && <InsightsTab run={r} manifest={manifest.data || null} insights={insights.data || null} />;
      case 'design': return resultsId && <DesignTab runId={resultsId} />;
      case 'compare': return r && <CompareTab run={r} manifest={manifest.data || null} />;
      case 'downloads': return r && <DownloadsTab run={r} manifest={manifest.data || null} />;
      case 'info': return r && <InfoTab simulation={sim!} run={r} />;
      case 'progress': return <ProgressView simulation={sim!} />;
      default: return null;
    }
  }
}
