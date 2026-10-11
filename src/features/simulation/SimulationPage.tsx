import { useState } from 'react';
import { Link, useLocation } from 'wouter';
import * as Tabs from '@radix-ui/react-tabs';
import { useQueryClient } from '@tanstack/react-query';
import { ArrowLeft, Copy, Pencil } from 'lucide-react';
import { api, patch, post } from '../../api/client';
import { qk, useForces, useInsights, useManifest, useRun, useSimulation } from '../../api/queries';
import { EmptyState, EvidenceStrip, ExperimentalBadge, Spinner, cx, toast } from '../../ui';
import { StatusBadge } from '../dashboard/DashboardPage';
import { ProgressView, StageTimeline } from './ProgressView';
import { FlowTab } from '../results/FlowTab';
import { ForcesTab } from '../results/ForcesTab';
import { InsightsTab } from '../results/InsightsTab';
import { CompareTab } from '../results/CompareTab';
import { DownloadsTab, ElementsTab, InfoTab } from '../results/OtherTabs';
import { DesignTab } from '../results/DesignTab';
import { isShare } from '../../share/bundle';

const USE_CASE_LABEL: Record<string, string> = { aerodynamics: 'External aerodynamics', propeller: 'Rotor', pipe: 'Internal flow', custom: 'Custom' };
const ALL_TABS = [['flow', 'Flow'], ['forces', 'Forces'], ['elements', 'Elements'], ['insights', 'Insights'], ['design', 'Design'], ['compare', 'Compare'], ['downloads', 'Report & downloads'], ['info', 'Info'], ['progress', 'Run']] as const;
// A shared report is read-only and offline: no comparisons, downloads or live run view.
const SHARE_HIDDEN = ['compare', 'downloads', 'progress'];

export default function SimulationPage({ id, tab }: { id: string; tab?: string }) {
  const simulation = useSimulation(id);
  const sim = simulation.data;
  const resultsId = sim?.results_run_id || null;
  const run = useRun(resultsId);
  const manifest = useManifest(resultsId, !!run.data?.result?.package);
  const forces = useForces(run.data?.result?.package ? resultsId : null);
  const insights = useInsights(run.data?.result?.package ? resultsId : null);
  const [, navigate] = useLocation();
  const client = useQueryClient();
  const [editing, setEditing] = useState(false);

  if (simulation.isError) return <EmptyState title="Simulation not found"><Link href="/" className="btn-primary mt-2">Back to simulations</Link></EmptyState>;
  if (!sim) return <div className="grid flex-1 place-items-center"><Spinner label="Loading simulation" /></div>;
  const hasResults = !!resultsId && !!run.data;
  const showDesign = !!sim?.setup_summary.design || !!run.data?.spec?.design?.enabled;
  const TABS = ALL_TABS.filter(([key]) => (showDesign || key !== 'design') && !(isShare && SHARE_HIDDEN.includes(key)));
  const active = tab && TABS.some(([k]) => k === tab) ? tab : hasResults ? 'flow' : 'progress';
  const legacy = hasResults && !run.data!.result?.package && sim.status === 'completed';

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

  return <div className="mx-auto flex w-full max-w-[1800px] flex-1 flex-col gap-4 px-4 py-4">
    <header className="flex flex-wrap items-start gap-3">
      {!isShare && <Link href="/" className="btn-ghost btn-icon mt-0.5" aria-label="Back to simulations"><ArrowLeft size={18} /></Link>}
      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          {editing ? <input autoFocus className="input max-w-md text-lg font-semibold" defaultValue={sim.name} onBlur={(e) => rename(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') rename((e.target as HTMLInputElement).value); if (e.key === 'Escape') setEditing(false); }} />
            : <h1 className="truncate text-xl font-semibold">{sim.name}</h1>}
          {!editing && !isShare && <button className="btn-ghost btn-icon" aria-label="Rename" onClick={() => setEditing(true)}><Pencil size={14} /></button>}
          <StatusBadge status={sim.status} />{sim.experimental.length > 0 && <ExperimentalBadge />}
          {sim.tags.map((t) => <span key={t} className="chip">{t}</span>)}
        </div>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
          <span>{sim.geometry.filename}</span><span>{USE_CASE_LABEL[sim.setup_summary.use_case] || sim.setup_summary.use_case} · {sim.setup_summary.fluid} · {sim.setup_summary.speed_m_s} m/s</span>
          <span>{sim.setup_summary.tier[0].toUpperCase() + sim.setup_summary.tier.slice(1)} accuracy</span>{sim.cells && <span>{sim.cells.toLocaleString()} cells</span>}{sim.convergence && <span>{sim.convergence}</span>}
        </div>
        <EvidenceStrip block={sim.status_block} />
      </div>
      {!isShare && <button className="btn-secondary" onClick={duplicate}><Copy size={15} />Duplicate & edit</button>}
    </header>
    {!hasResults && sim.status !== 'completed' ? <ProgressView simulation={sim} /> :
      <Tabs.Root value={active} onValueChange={(value) => navigate(`/simulations/${id}/${value}`, { replace: true })} className="flex flex-1 flex-col gap-4">
        <Tabs.List className="flex gap-1 overflow-x-auto border-b border-line scrollbar-thin" aria-label="Results" data-tour="results-tabs">
          {TABS.map(([key, label]) => <Tabs.Trigger key={key} value={key} className={cx('-mb-px whitespace-nowrap border-b-2 px-3 py-2 text-sm font-semibold transition-colors',
            active === key ? 'border-accent text-ink' : 'border-transparent text-muted hover:text-ink')}>{label}{key === 'insights' && insights.data?.insights.length ? <span className="ml-1.5 rounded-full bg-accent-soft px-1.5 text-xs text-accent-ink">{insights.data.insights.length}</span> : null}</Tabs.Trigger>)}
        </Tabs.List>
        {sim.status !== 'completed' && <div className="flex items-center gap-3"><StageTimeline simulation={sim} /><span className="hint">{sim.stage === 'mesh' || sim.stage === 'review' ? 'A finer mesh is being built from the last solution.' : 'The results package and report are still being built.'}</span></div>}
        <Tabs.Content value="flow" className="flex flex-1 flex-col outline-none">{run.data && <FlowTab run={run.data} manifest={manifest.data || null} legacy={legacy} />}</Tabs.Content>
        <Tabs.Content value="forces" className="outline-none">{run.data && <ForcesTab run={run.data} forces={forces.data || null} />}</Tabs.Content>
        <Tabs.Content value="elements" className="outline-none">{run.data && <ElementsTab run={run.data} />}</Tabs.Content>
        <Tabs.Content value="insights" className="flex flex-1 flex-col outline-none">{run.data && <InsightsTab run={run.data} manifest={manifest.data || null} insights={insights.data || null} />}</Tabs.Content>
        <Tabs.Content value="design" className="outline-none">{resultsId && <DesignTab runId={resultsId} />}</Tabs.Content>
        <Tabs.Content value="compare" className="outline-none">{run.data && <CompareTab run={run.data} manifest={manifest.data || null} />}</Tabs.Content>
        <Tabs.Content value="downloads" className="outline-none">{run.data && <DownloadsTab run={run.data} manifest={manifest.data || null} />}</Tabs.Content>
        <Tabs.Content value="info" className="outline-none">{run.data && <InfoTab simulation={sim} run={run.data} />}</Tabs.Content>
        <Tabs.Content value="progress" className="outline-none"><ProgressView simulation={sim} /></Tabs.Content>
      </Tabs.Root>}
  </div>;
}
