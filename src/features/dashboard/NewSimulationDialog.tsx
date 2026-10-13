import { useState } from 'react';
import { useLocation } from 'wouter';
import { useQueryClient } from '@tanstack/react-query';
import { Box, FileUp, Sparkles, UploadCloud } from 'lucide-react';
import { api, post } from '../../api/client';
import { qk, useCapabilities, useGeometries, useSamples } from '../../api/queries';
import type { Geometry, Project, Run } from '../../api/types';
import { Dialog, ProgressBar, Segmented, cx, toast } from '../../ui';

const ACCEPT = '.step,.stp,.iges,.igs,.stl,.obj,.gltf,.glb';

/** Start a setup from a geometry revision: a project (draft workspace) the setup page edits. */
export async function startSetup(geometry: Geometry, name?: string): Promise<Project> {
  return post<Project>('/projects', { name: name || geometry.filename.replace(/\.[^.]+$/, ''), workspace: { version: 2, geometry_id: geometry.id, spec: null } });
}

async function waitForJob(id: string, onProgress: (label: string) => void): Promise<Run> {
  for (;;) {
    const job = await api<Run>(`/jobs/${id}`);
    if (job.status === 'completed') return job;
    if (['failed', 'cancelled', 'interrupted'].includes(job.status)) throw new Error(job.error || 'Import failed');
    onProgress(job.stage === 'importing' ? 'Converting and splitting the model into parts…' : 'Waiting for the CAD worker…');
    await new Promise((r) => setTimeout(r, 1200));
  }
}

export function NewSimulationDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const [tab, setTab] = useState<'import' | 'samples' | 'library'>('import');
  const [units, setUnits] = useState('mm');
  const [busy, setBusy] = useState('');
  const [drag, setDrag] = useState(false);
  const samples = useSamples();
  const geometries = useGeometries();
  const capabilities = useCapabilities();
  const client = useQueryClient();
  const [, navigate] = useLocation();

  async function open_(geometry: Geometry, name?: string) {
    const project = await startSetup(geometry, name);
    client.invalidateQueries({ queryKey: qk.projects });
    client.invalidateQueries({ queryKey: qk.geometries });
    onOpenChange(false);
    navigate(`/setup/${project.id}`);
  }

  async function upload(file: File) {
    setBusy(`Uploading ${file.name}…`);
    try {
      const form = new FormData();
      form.append('file', file);
      form.append('units', units);
      let geometry: Geometry;
      if (capabilities.data?.features.async_import) {
        const job = await post<Run>('/geometry/import-jobs', form);
        const done = await waitForJob(job.id, setBusy);
        geometry = await api<Geometry>(`/geometry/${done.result.geometry_id}`);
      } else {
        setBusy('Converting and splitting the model into parts…');
        geometry = await post<Geometry>('/geometry/import', form);
      }
      await open_(geometry);
    } catch (e) {
      toast.error(e);
    } finally {
      setBusy('');
    }
  }

  async function sample(id: string, name: string) {
    setBusy(`Creating ${name}…`);
    try { await open_(await post<Geometry>(`/samples/${id}`), name); } catch (e) { toast.error(e); } finally { setBusy(''); }
  }

  return <Dialog open={open} onOpenChange={onOpenChange} title="New simulation" description="Start from your own CAD, a sample model, or a model you imported before. Everything stays on this computer." wide>
    <div className="mb-4"><Segmented label="Source" value={tab} onChange={setTab} options={[
      { value: 'import', label: <span className="flex items-center gap-1.5"><FileUp size={15} />Import CAD</span> },
      { value: 'samples', label: <span className="flex items-center gap-1.5"><Sparkles size={15} />Samples</span> },
      { value: 'library', label: <span className="flex items-center gap-1.5"><Box size={15} />Your models</span> },
    ]} /></div>
    {busy ? <div className="space-y-3 py-10"><ProgressBar value={60} label={busy} /></div> : <>
      {tab === 'import' && <div className="space-y-4">
        <label onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
          onDrop={(e) => { e.preventDefault(); setDrag(false); const f = e.dataTransfer.files?.[0]; if (f) upload(f); }}
          className={cx('flex cursor-pointer flex-col items-center justify-center gap-2 rounded-2xl border-2 border-dashed px-6 py-12 text-center transition-colors', drag ? 'border-accent bg-accent-soft' : 'border-line-strong bg-surface-2 hover:border-accent')}
          data-tour="import-dropzone">
          <UploadCloud size={34} className="text-accent" />
          <span className="text-base font-semibold">Drop a 3D model here or click to browse</span>
          <span className="hint">STEP, IGES, STL, OBJ, glTF / GLB · up to 100 MB. Assemblies keep their parts, so you can pick wheels, rotors or the body later.</span>
          <input type="file" className="sr-only" accept={ACCEPT} onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ''; }} />
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <span className="label mb-0">Units for STL / OBJ files</span>
          <Segmented size="sm" label="Model units" value={units} onChange={setUnits} options={['mm', 'cm', 'm', 'in'].map((u) => ({ value: u, label: u }))} />
          <span className="hint">STEP and IGES carry their own units; glTF is in metres.</span>
        </div>
      </div>}
      {tab === 'samples' && <div className="grid gap-3 sm:grid-cols-2">
        {(samples.data || []).map((s) => <button key={s.id} onClick={() => sample(s.id, s.name)} className="card card-pad text-left transition hover:border-accent hover:shadow-md">
          <div className="flex items-center justify-between gap-2"><span className="font-semibold">{s.name}</span>
            <span className="chip shrink-0">{s.use_case === 'pipe' ? 'Internal flow' : s.use_case === 'propeller' ? 'Rotor' : 'External flow'}</span></div>
          <p className="hint mt-1.5">{s.description}</p>
          {(s.shows?.length || s.minutes) ? <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
            {s.shows?.map((item) => <span key={item} className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent-ink">{item}</span>)}
            {s.minutes && <span className="ml-auto text-[11px] text-muted">typically ≈ {s.minutes} min (estimate)</span>}
          </div> : null}
        </button>)}
      </div>}
      {tab === 'library' && <div className="max-h-[50vh] space-y-1.5 overflow-y-auto scrollbar-thin">
        {(geometries.data || []).length === 0 && <p className="hint py-8 text-center">Models you import appear here.</p>}
        {(geometries.data || []).map((g) => <button key={g.id} onClick={() => open_(g).catch(toast.error)} className="flex w-full items-center gap-3 rounded-xl border border-line px-3.5 py-2.5 text-left hover:border-accent hover:bg-surface-2">
          <Box size={18} className="text-muted" />
          <span className="min-w-0 flex-1"><span className="block truncate text-sm font-semibold">{g.filename}</span>
            <span className="hint">{g.dimensions.map((d) => d.toPrecision(3)).join(' × ')} m · {g.patches.length} surfaces{g.geometry_fidelity === 'wrapped' ? ' · wrapped' : ''}{g.parent_id ? ' · prepared revision' : ''}</span></span>
        </button>)}
      </div>}
    </>}
  </Dialog>;
}
