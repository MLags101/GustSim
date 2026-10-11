import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { Link, useLocation } from 'wouter';
import { ArrowLeft, Check, CircleAlert, Clock, Cpu, Download, Layers3, MemoryStick, Rocket, RotateCw, Wand2 } from 'lucide-react';
import { post, download } from '../../api/client';
import { useHealth } from '../../api/queries';
import type { Finding, Geometry, Vec3 } from '../../api/types';
import { usePreferences } from '../../state/preferences';
import { formatQuantity, significant } from '../../lib/units';
import { transformMatrix } from '../../lib/euler';
import { Badge, Check as CheckBox, EmptyState, ExperimentalBadge, Field, Notice, NumberField, QuantityField, Segmented, SelectField, Spinner, VectorField, cx, toast } from '../../ui';
import { ViewerFrame } from '../../viewer/ViewerFrame';
import { useViewerTheme } from '../../viewer/theme';
import { ComponentList } from './ComponentList';
import { DesignEditor, OpeningsEditor, RadiatorsEditor, RotorsEditor, WheelsEditor } from './ElementsEditor';
import { BatchDialog } from '../batches/BatchDialog';
import { useAgentBridge } from '../../agent/BrowserTools';
import { useSetup, type SetupState, type Tier } from './useSetup';

function Step({ n, id, title, description, children, aside }: { n: number; id: string; title: string; description?: string; children: ReactNode; aside?: ReactNode }) {
  return <section id={id} className="card scroll-mt-20" data-tour={`setup-${id}`}>
    <header className="flex items-start gap-3 border-b border-line px-4 py-3 sm:px-5">
      <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-accent-soft text-sm font-bold text-accent-ink">{n}</span>
      <div className="min-w-0 flex-1"><h2 className="text-[15px] font-semibold">{title}</h2>{description && <p className="hint mt-0.5">{description}</p>}</div>
      {aside}
    </header>
    <div className="space-y-4 p-4 sm:p-5">{children}</div>
  </section>;
}

const DIRECTIONS: [string, Vec3, Vec3][] = [
  ['+X', [1, 0, 0], [0, 0, 1]], ['−X', [-1, 0, 0], [0, 0, 1]], ['+Y', [0, 1, 0], [0, 0, 1]],
  ['−Y', [0, -1, 0], [0, 0, 1]], ['+Z', [0, 0, 1], [1, 0, 0]], ['−Z', [0, 0, -1], [1, 0, 0]],
];
/** Unit flow direction from the spec's angles (mirrors models.Flow.velocity). */
const flowDirection = (alphaDeg: number, betaDeg: number) => {
  const a = (alphaDeg * Math.PI) / 180, b = (betaDeg * Math.PI) / 180;
  return [Math.cos(a) * Math.cos(b), Math.sin(b), Math.sin(a) * Math.cos(b)];
};
const same = (a: number[], b: number[]) => a.every((v, i) => Math.abs(v - b[i]) < 1e-9);

function GeometryHealth({ geometry }: { geometry: Geometry }) {
  const d = geometry.diagnostics;
  const items: [boolean | 'review', string][] = [
    [d.watertight, d.watertight ? 'Closed surface' : 'Surface has openings: prepare or wrap it before meshing'],
    [d.winding_consistent, d.winding_consistent ? 'Consistent normals' : 'Inconsistent normals: run preparation'],
    [!d.degenerate_faces, d.degenerate_faces ? `${d.degenerate_faces} degenerate triangles: run preparation` : 'No degenerate triangles'],
    [d.nonmanifold_edges ? 'review' : true, d.nonmanifold_edges ? `${d.nonmanifold_edges} non-manifold edges where parts meet (meshable; wrap for one clean surface)` : 'Manifold surface'],
  ];
  return <ul className="grid gap-1.5 sm:grid-cols-2">{items.map(([ok, text]) => <li key={text} className="flex items-start gap-2 text-sm">
    {ok === true ? <Check size={15} className="mt-0.5 shrink-0 text-success" /> : <CircleAlert size={15} className={cx('mt-0.5 shrink-0', ok === 'review' ? 'text-warning' : 'text-danger')} />}
    <span className="text-ink-2">{text}</span></li>)}</ul>;
}

function ModelSection({ s, hidden, setHidden, transform, setTransform }: { s: SetupState; hidden: string[]; setHidden: (v: string[]) => void; transform: { rotation: Vec3; scale: number }; setTransform: (t: { rotation: Vec3; scale: number }) => void }) {
  const geometry = s.geometry.data!;
  const intent = s.intent!;
  const [busy, setBusy] = useState('');
  const [candidate, setCandidate] = useState<Geometry | null>(null);
  const [wrap, setWrap] = useState({ resolution: 256, gaps: 2 });
  async function prepare(body: Record<string, unknown>, label: string, adopt = false) {
    setBusy(label);
    try {
      const next = await post<Geometry>(`/geometry/${geometry.id}/prepare`, body);
      if (adopt) { s.switchRevision(next); setTransform({ rotation: [0, 0, 0], scale: 1 }); toast.success('A new geometry revision is in use; the original is kept.'); }
      else setCandidate(next);
    } catch (e) { toast.error(e); } finally { setBusy(''); }
  }
  const selectLabel = intent.kind === 'propeller' ? 'rotate' : intent.kind === 'pipe' ? 'are walls' : 'measured for forces';
  const transformed = !same(transform.rotation, [0, 0, 0]) || transform.scale !== 1;
  return <Step n={1} id="geometry-diagnostics" title="Model" description="Check the surface, orient the model and choose the parts to measure.">
    <div className="flex flex-wrap items-center gap-x-6 gap-y-1 text-sm">
      <span className="font-semibold">{geometry.filename}</span>
      <span className="text-muted">{geometry.dimensions.map((d) => significant(d, 3)).join(' × ')} m</span>
      <span className="text-muted">{geometry.triangles.toLocaleString()} triangles · {geometry.patches.length} surfaces</span>
      {geometry.geometry_fidelity === 'wrapped' && <Badge tone="warn" title={geometry.wrap?.detail}>Wrapped approximation</Badge>}
    </div>
    <GeometryHealth geometry={geometry} />
    {candidate ? <Notice tone="active" title="Prepared revision ready">
      Triangles {geometry.triangles.toLocaleString()} → {candidate.triangles.toLocaleString()}; open edges {geometry.diagnostics.open_edges} → {candidate.diagnostics.open_edges}; {candidate.diagnostics.watertight ? 'closed surface' : 'still open'}.
      {candidate.geometry_fidelity === 'wrapped' && ' This is a wrapped approximation: loads will be computed on the wrap.'}
      <div className="mt-2 flex gap-2"><button className="btn-primary btn-sm" onClick={() => { s.switchRevision(candidate); setCandidate(null); }}>Use this revision</button><button className="btn-secondary btn-sm" onClick={() => setCandidate(null)}>Keep the original</button></div>
    </Notice> : <div className="flex flex-wrap gap-2">
      <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => prepare({ repair: true, component_surfaces: true }, 'Preparing')}><Wand2 size={14} />Prepare for meshing</button>
      <button className="btn-secondary btn-sm" disabled={!!busy} onClick={() => prepare({ repair: true }, 'Repairing')}>Repair only (keep faces)</button>
      {busy && <Spinner label={`${busy}…`} />}
    </div>}
    <details className="rounded-xl border border-line px-3 py-2">
      <summary className="cursor-pointer text-sm font-semibold">Close a difficult assembly (wrap)</summary>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <NumberField label="Detail · cells across the model" value={wrap.resolution} min={32} max={1024} step={16} onChange={(v) => setWrap({ ...wrap, resolution: v })} />
        <NumberField label="Seal gaps up to · cells" value={wrap.gaps} min={0} max={64} step={1} onChange={(v) => setWrap({ ...wrap, gaps: v })} />
      </div>
      <p className="hint mt-2">Builds one closed surface on a uniform grid. Detail finer than a cell is lost and narrow openings close; the revision stays labelled as wrapped on every result and report.</p>
      <button className="btn-secondary btn-sm mt-2" disabled={!!busy} onClick={() => prepare({ repair: true, component_surfaces: true, wrap: true, wrap_resolution: wrap.resolution, wrap_close_gaps: wrap.gaps }, 'Wrapping')}>Preview wrapped surface</button>
    </details>
    <div>
      <h3 className="label">Orientation and scale</h3>
      <div className="grid gap-3 sm:grid-cols-[1fr_9rem]">
        <VectorField label="Rotation · degrees about X, Y, Z (applied in that order)" value={transform.rotation} onChange={(v) => setTransform({ ...transform, rotation: v })} />
        <NumberField label="Scale" value={transform.scale} min={0.000001} onChange={(v) => setTransform({ ...transform, scale: v })} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {[['Yaw 90°', [0, 0, 90]], ['Pitch 90°', [0, 90, 0]], ['Roll 90°', [90, 0, 0]]].map(([label, r]) => <button key={label as string} className="btn-ghost btn-sm" onClick={() => setTransform({ ...transform, rotation: transform.rotation.map((v, i) => v + (r as number[])[i]) as Vec3 })}><RotateCw size={13} />{label as string}</button>)}
        {transformed && <><span className="chip">Preview only</span>
          <button className="btn-primary btn-sm" disabled={!!busy} onClick={() => prepare({ rotation_deg: transform.rotation, scale: transform.scale }, 'Applying', true)}>Apply as new revision</button>
          <button className="btn-ghost btn-sm" onClick={() => setTransform({ rotation: [0, 0, 0], scale: 1 })}>Reset</button></>}
      </div>
      <p className="hint mt-1">The flow comes along the travel direction you choose below; rotate the model when its CAD axes differ (yaw for crosswind).</p>
    </div>
    <div>
      <h3 className="label">Parts</h3>
      <ComponentList geometry={geometry} selected={intent.patches} onSelect={(patches) => s.setIntent({ patches })} hidden={hidden} onHide={setHidden} selectLabel={selectLabel} />
    </div>
  </Step>;
}

function TypeSection({ s }: { s: SetupState }) {
  const intent = s.intent!;
  const time = s.overrides.time || { mode: 'steady' as const };
  return <Step n={2} id="guided-setup" title="Simulation type" description="What is being simulated, and in which fluid.">
    <div id="simulation-template" className="space-y-4">
      <Field label="Case">
        <Segmented label="Case" value={intent.kind} onChange={(kind) => s.setIntent({ kind, speed: kind === 'propeller' ? 0 : kind === 'pipe' ? 1 : 20 })} options={[
          { value: 'aerodynamics', label: 'Object in a flow' }, { value: 'propeller', label: 'Rotor / propeller' }, { value: 'pipe', label: 'Internal flow' }]} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Fluid"><Segmented label="Fluid" value={intent.fluid} onChange={(fluid) => {
          if (fluid === 'multiphase') {
            const bounds = s.geometry.data?.bounds;
            const waterline_z = bounds ? bounds[0][2] + 0.4 * (bounds[1][2] - bounds[0][2]) : 0;
            s.setIntent({ fluid, waterline_z: intent.waterline_z ?? waterline_z });
          } else s.setIntent({ fluid });
        }} options={[
          { value: 'air', label: 'Air' }, { value: 'water', label: 'Water' },
          { value: 'multiphase', label: 'Water + air', disabled: intent.kind !== 'aerodynamics', title: intent.kind !== 'aerodynamics' ? 'A free surface is for an object in a flow.' : 'Experimental: calm water, fixed trim and sinkage.' }]} /></Field>
        <div className="sm:col-span-2"><Field label="Ground" hint={intent.kind !== 'aerodynamics' ? 'A ground plane applies to objects in a flow.' : intent.ground && intent.ground !== 'none' ? 'Experimental: the floor is the bottom of the wind tunnel; a moving floor runs with the air like a rolling-road wind tunnel.' : undefined}>
          <Segmented label="Ground" value={intent.kind === 'aerodynamics' ? intent.ground || 'none' : 'none'} onChange={(ground) => s.setIntent({ ground, direction: [intent.direction[0], intent.direction[1], 0] as Vec3, lift_axis: [0, 0, 1] })} options={[
          { value: 'none', label: 'Above ground' }, { value: 'static', label: 'On ground', disabled: intent.kind !== 'aerodynamics' }, { value: 'moving', label: 'Moving ground', disabled: intent.kind !== 'aerodynamics' }]} /></Field></div>
        {intent.kind === 'aerodynamics' && intent.ground && intent.ground !== 'none' && <QuantityField label="Ride height (gap under the model)" quantity="length" value={intent.ride_height_m || 0} min={0} onChange={(ride_height_m) => s.setIntent({ ride_height_m })} hint="0 places the model on the floor; a vehicle's tyres then touch the ground." />}
        {intent.fluid === 'multiphase' && <QuantityField label="Waterline" quantity="length" value={intent.waterline_z || 0} onChange={(waterline_z) => s.setIntent({ waterline_z })} hint="Experimental. Calm water at this height; the hull does not sink or trim." />}
        <Field label="Time" hint={time.mode === 'transient' ? (intent.kind === 'propeller' ? 'Experimental sliding mesh: the rotor turns in time. The propeller gate has not been run.' : 'Experimental: a steady start, then time-accurate flow with averaged loads, fluctuations, a shedding frequency and animations.') : undefined}>
          <Segmented label="Time" value={time.mode} onChange={(mode) => s.setOverrides({ time: { ...time, mode, sliding_mesh: mode === 'transient' && intent.kind === 'propeller' } })} options={[
          { value: 'steady', label: 'Steady (averaged)' },
          { value: 'transient', label: 'Transient' }]} /></Field>
        {time.mode === 'transient' && <div className="grid gap-3 sm:col-span-2 sm:grid-cols-3">
          <NumberField label="Simulated time" unit="flow-throughs" value={time.flow_throughs ?? 8} min={1} max={100} step={1} onChange={(flow_throughs) => s.setOverrides({ time: { ...time, flow_throughs } })}
            hint="Times the air crosses the whole wind tunnel." />
          <NumberField label="Average after" unit="%" value={Math.round((time.average_after_fraction ?? 0.3)*100)} min={0} max={90} step={5}
            onChange={(v) => s.setOverrides({ time: { ...time, average_after_fraction: v/100 } })} hint="The start-up is excluded from averages." />
          <NumberField label="Animation frames" value={time.frames ?? 60} min={0} max={300} step={10} onChange={(frames) => s.setOverrides({ time: { ...time, frames } })}
            hint="Snapshots of surface pressure and a velocity section." />
        </div>}
        <Field label="Turbulence"><Segmented label="Turbulence" value={intent.turbulence} onChange={(turbulence) => s.setIntent({ turbulence })} options={[
          { value: 'kOmegaSST', label: 'k–ω SST' }, { value: 'laminar', label: 'Laminar' }]} /></Field>
      </div>
      <p className="hint">Steady RANS (k–ω SST, fully turbulent, wall functions) predicts time-averaged loads. Transition, compressibility and cavitation are outside this solver template.</p>
    </div>
  </Step>;
}

function FlowSection({ s }: { s: SetupState }) {
  const intent = s.intent!;
  const geometry = s.geometry.data!;
  const patches = geometry.patches.map((p) => ({ value: p.name, label: p.name }));
  return <Step n={3} id="incident-flow" title={intent.kind === 'pipe' ? 'Flow through the model' : intent.kind === 'propeller' ? 'Rotor and inflow' : 'Speed and direction'}>
    {intent.kind === 'aerodynamics' && <>
      <QuantityField label="Travel speed" quantity="speed" value={intent.speed} min={0} onChange={(speed) => s.setIntent({ speed })} />
      <Field label="Direction of travel" hint="The model moves this way; air comes from the opposite side. Arrows in the 3D view show the incoming flow.">
        <div className="flex flex-wrap gap-1.5">{DIRECTIONS.map(([label, direction, lift]) => <button key={label} className={cx('btn-secondary btn-sm', same(intent.direction, direction) && 'border-accent bg-accent-soft text-accent-ink')} onClick={() => s.setIntent({ direction, lift_axis: lift })}>{label}</button>)}</div>
      </Field>
      <details><summary className="cursor-pointer text-sm font-semibold text-ink-2">Custom directions</summary>
        <div className="mt-3 grid gap-3"><VectorField label="Travel direction" value={intent.direction} onChange={(direction) => s.setIntent({ direction })} />
          <VectorField label="Lift direction (perpendicular to travel)" value={intent.lift_axis} onChange={(lift_axis) => s.setIntent({ lift_axis })} /></div></details>
    </>}
    {intent.kind === 'propeller' && <>
      <div className="grid gap-3 sm:grid-cols-2">
        <QuantityField label="Rotational speed (signed, right-hand rule)" quantity="rotation" value={intent.rpm} onChange={(rpm) => s.setIntent({ rpm })} />
        <QuantityField label="Axial inflow (0 = still fluid)" quantity="speed" value={intent.speed} min={0} onChange={(speed) => s.setIntent({ speed })} />
      </div>
      <VectorField label="Rotation axis" value={intent.axis} onChange={(axis) => s.setIntent({ axis })} />
      <VectorField label="Rotation centre · m" value={intent.origin} onChange={(origin) => s.setIntent({ origin })} hint="Select the rotating parts in Model; the rotating region is fitted around them." />
    </>}
    {intent.kind === 'pipe' && <>
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField label="Inlet surface" value={intent.inlet} onChange={(inlet) => s.setIntent({ inlet })} options={[{ value: '', label: 'Choose the inlet cap' }, ...patches]} />
        <SelectField label="Outlet surface" value={intent.outlet} onChange={(outlet) => s.setIntent({ outlet })} options={[{ value: '', label: 'Choose the outlet cap' }, ...patches]} />
      </div>
      <Field label="Driving condition"><Segmented label="Driving condition" value={intent.driving} onChange={(driving) => s.setIntent({ driving })} options={[
        { value: 'speed', label: 'Inlet speed' }, { value: 'flow', label: 'Volume flow' }, { value: 'pressure', label: 'Pressures' }]} /></Field>
      {intent.driving === 'flow' ? <QuantityField label="Volume flow" quantity="flow" value={intent.flow_rate} onChange={(flow_rate) => s.setIntent({ flow_rate })} />
        : <QuantityField label={intent.driving === 'pressure' ? 'Reference speed estimate (turbulence only)' : 'Mean inlet speed'} quantity="speed" value={intent.speed} onChange={(speed) => s.setIntent({ speed })} />}
      {intent.driving === 'pressure' && <QuantityField label="Inlet total pressure (gauge)" quantity="pressure" value={intent.inlet_pressure_pa} onChange={(inlet_pressure_pa) => s.setIntent({ inlet_pressure_pa })} />}
      <QuantityField label="Outlet static pressure (gauge)" quantity="pressure" value={intent.outlet_pressure_pa} onChange={(outlet_pressure_pa) => s.setIntent({ outlet_pressure_pa })} />
    </>}
  </Step>;
}

function ElementsSection({ s }: { s: SetupState }) {
  const intent = s.intent!;
  const rotation = (s.spec as any)?.rotation;
  return <Step n={4} id="rotating-region" title="Elements" description="Wheels, rotors, radiators and openings. Each element is experimental until its benchmark passes.">
    {intent.kind === 'propeller' ? <div className="rounded-xl border border-line bg-surface-2 p-3.5 text-sm">
      <div className="flex items-center gap-2 font-semibold"><RotateCw size={16} className="text-coral" />Rotating region (steady MRF)</div>
      {rotation?.enabled ? <p className="mt-1 text-ink-2">{intent.patches.length} rotating surfaces · {significant(intent.rpm, 5)} RPM · region radius {significant(rotation.radius, 3)} m, length {significant(rotation.length, 3)} m · blade radius {significant(rotation.blade_radius, 3)} m.</p>
        : <p className="hint mt-1">Select the rotating parts under Model to fit the region.</p>}
      <p className="hint mt-1">A frozen-rotor (MRF) solution predicts mean thrust and torque at one blade position; blade-passing unsteadiness is not resolved.</p>
    </div> : null}
    {intent.kind === 'propeller' && <RotorsEditor s={s} />}
    {intent.kind === 'aerodynamics' && <WheelsEditor s={s} />}
    {intent.kind !== 'pipe' && <RadiatorsEditor s={s} />}
    {intent.kind !== 'propeller' && <OpeningsEditor s={s} />}
    {intent.kind === 'aerodynamics' && <DesignEditor s={s} />}
  </Step>;
}

const TIER_TEXT: Record<string, string> = {
  basic: 'Quick concept comparisons. Coarse surface cells; trends over absolute values.',
  regular: 'Design work. Finer surface and a nested wake refinement.',
  advanced: 'Detailed study. Fine cells and three wake boxes; limited by this computer’s memory.',
};

function AccuracySection({ s }: { s: SetupState }) {
  const intent = s.intent!;
  const estimate = s.estimate.data;
  const units = usePreferences((u) => u.units);
  const minutes = (m: number) => m < 60 ? `${Math.max(1, Math.round(m))} min` : `${(m / 60).toFixed(1)} h`;
  return <Step n={5} id="mesh-resolution" title="Accuracy" description="Sets the mesh and solver effort. Estimates calibrate from your past runs on this computer." aside={estimate && <span className="chip">{estimate.basis === 'calibrated' ? `Calibrated · ${estimate.samples} runs` : 'Heuristic estimate'}</span>}>
    <div className="grid gap-3 md:grid-cols-3" role="radiogroup" aria-label="Accuracy">
      {(['basic', 'regular', 'advanced'] as Tier[]).map((tier) => {
        const e = estimate?.tiers.find((t) => t.tier === tier);
        const active = intent.tier === tier;
        return <button key={tier} role="radio" aria-checked={active} onClick={() => s.setIntent({ tier })}
          className={cx('rounded-xl border p-3.5 text-left transition', active ? 'border-accent bg-accent-soft ring-2 ring-accent/30' : 'border-line hover:border-accent/60')}>
          <div className="flex items-center justify-between"><span className="font-semibold capitalize">{tier}</span>{active && <Check size={16} className="text-accent" />}</div>
          <p className="hint mt-1 min-h-[2.5rem]">{TIER_TEXT[tier]}</p>
          {e ? <dl className="mt-2 space-y-1 text-xs text-ink-2">
            <div className="flex items-center gap-1.5"><Layers3 size={13} className="text-muted" />{significant(e.cells.expected / 1e6, 2)} M cells</div>
            <div className="flex items-center gap-1.5"><Clock size={13} className="text-muted" />~{minutes(e.runtime_min.mesh + e.runtime_min.solve + e.runtime_min.post)}</div>
            <div className="flex items-center gap-1.5"><MemoryStick size={13} className="text-muted" />{significant(Math.max(e.memory_gb.mesh, e.memory_gb.solve), 2)} GB</div>
            {e.time_steps != null && <div className="text-muted">≈ {significant(e.time_steps, 2)} time steps</div>}
            {!e.feasible && <div className="text-danger">{e.reason}</div>}
          </dl> : <div className="mt-2"><Spinner label="Estimating" /></div>}
        </button>;
      })}
    </div>
    <div className="flex items-center gap-2">
      <button className={cx('btn-ghost btn-sm', intent.tier === 'custom' && 'bg-surface-3')} onClick={() => s.setIntent({ tier: 'custom' })}><Cpu size={14} />Custom mesh settings</button>
      {estimate && <span className="hint">Surface cells ≈ {formatQuantity('length', estimate.surface_cell_m, units.length === 'm' ? 'mm' : units.length, 3)} · first layer {formatQuantity('length', estimate.first_layer_m, 'mm', 2)}</span>}
    </div>
    {intent.tier === 'custom' && <div className="grid gap-3 rounded-xl border border-line p-3 sm:grid-cols-3">
      {([['base_cells', 'Background cells (longest side)'], ['surface_level', 'Surface refinement level'], ['feature_level', 'Edge refinement level'], ['body_level', 'Body box level'], ['layers', 'Near-wall layers'], ['max_cells', 'Cell budget']] as const).map(([key, label]) =>
        <NumberField key={key} label={label} value={(s.overrides.mesh?.[key] ?? (s.spec as any)?.mesh?.[key]) as number} step={1} onChange={(v) => s.setOverrides({ mesh: { ...(s.overrides.mesh || {}), [key]: v } })} />)}
    </div>}
    <p className="hint">Accuracy tiers do not guarantee accuracy; a mesh study (Basic vs Regular vs Advanced) shows how much the result still depends on the mesh.</p>
  </Step>;
}

function FindingRow({ finding }: { finding: Finding }) {
  const tone = finding.status === 'fail' ? 'text-danger' : finding.status === 'review' ? 'text-warning' : finding.status === 'pass' ? 'text-success' : 'text-muted';
  return <li className="flex items-start gap-2 text-sm">
    <span className={cx('mt-0.5 shrink-0 text-xs font-bold uppercase', tone)}>{finding.status === 'fail' ? 'Fix' : finding.status === 'review' ? 'Review' : finding.status === 'pass' ? 'OK' : 'Note'}</span>
    <span className="flex-1 text-ink-2">{finding.detail}</span>
    {finding.control && finding.status !== 'pass' && <button className="text-xs font-semibold text-accent-ink hover:underline" onClick={() => document.getElementById(finding.control!)?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Go to</button>}
  </li>;
}

function ReviewSection({ s }: { s: SetupState }) {
  const spec: any = s.spec;
  const report = s.report;
  const health = useHealth();
  const [, navigate] = useLocation();
  const [busy, setBusy] = useState(false);
  const [batching, setBatching] = useState(false);
  const units = usePreferences((u) => u.units);
  const findings = (report?.findings || []).filter((f) => f.status !== 'pass');
  const failing = findings.filter((f) => f.status === 'fail');
  const canLaunch = !!report?.valid && s.attested.dims && s.acknowledged && !busy;
  const experimental = report?.physics_maturity === 'experimental';
  async function launch() {
    setBusy(true);
    try { const simulation = await s.launch(); navigate(`/simulations/${simulation.id}`); }
    catch (e) { toast.error(e); setBusy(false); }
  }
  async function caseOnly() {
    try { const run: any = await s.caseOnly(); await download(`/runs/${run.id}/export/case`, 'gustsim-case.zip'); } catch (e) { toast.error(e); }
  }
  const rows: [string, string][] = spec ? [
    ['Fluid', `${spec.fluid.name === 'water' ? 'Water' : 'Air'} · ρ ${significant(spec.fluid.density, 4)} kg/m³ · μ ${significant(spec.fluid.dynamic_viscosity, 3)} Pa·s`],
    ['Flow', `${formatQuantity('speed', spec.flow.speed, units.speed)} · ${spec.flow.turbulence === 'laminar' ? 'laminar' : 'k–ω SST'}`],
    ['Reynolds number', report?.reynolds ? significant(report.reynolds, 3) : '—'],
    ['Wind tunnel', spec.domain.maximum.map((v: number, i: number) => significant(v - spec.domain.minimum[i], 3)).join(' × ') + ' m'],
    ['Reference area · length', `${significant(spec.references.area, 4)} m² · ${significant(spec.references.length, 4)} m`],
  ] : [];
  return <Step n={6} id="reference-quantities" title="Review and launch" description="Confirm what GustSim inferred, then start the simulation.">
    {s.previewError ? <Notice tone="warn" title="Complete the setup">{s.previewError}</Notice> : !spec ? <Spinner label="Building the setup" /> : <>
      <dl className="grid gap-x-6 gap-y-1.5 text-sm sm:grid-cols-2">{rows.map(([k, v]) => <div key={k} className="flex justify-between gap-3 border-b border-line py-1"><dt className="text-muted">{k}</dt><dd className="text-right font-medium tabular-nums">{v}</dd></div>)}</dl>
      <details id="domain-fluid-region" className="rounded-xl border border-line px-3 py-2">
        <summary className="cursor-pointer text-sm font-semibold">Reference values and advanced settings</summary>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <VectorField label="A point inside the fluid · m" value={s.overrides.fluid_point || spec.domain.fluid_point} onChange={(v) => s.setOverrides({ fluid_point: v })} />
          <NumberField label="Reference area · m²" value={s.overrides.reference_area || spec.references.area} onChange={(v) => s.setOverrides({ reference_area: v })} hint="Defaults to the frontal area along the travel direction." />
          <NumberField label="Reference length · m" value={s.overrides.reference_length || spec.references.length} onChange={(v) => s.setOverrides({ reference_length: v })} />
          <NumberField label="Iteration limit" value={s.overrides.iterations || spec.solver.iterations} step={100} onChange={(v) => s.setOverrides({ iterations: v })} hint="Convergence detection usually stops earlier." />
          <NumberField label="CPU processes" value={s.overrides.processes || spec.solver.processes} step={1} min={1} max={64} onChange={(v) => s.setOverrides({ processes: v })} />
          <NumberField label="Automatic retries on failure" value={s.overrides.auto_retry ?? spec.solver.auto_retry ?? 0} step={1} min={0} max={3} onChange={(v) => s.setOverrides({ auto_retry: v })} hint="Each retry is a new attempt with recorded setting changes." />
        </div>
        <details className="mt-3"><summary className="cursor-pointer text-xs font-semibold text-muted">Full configuration (JSON)</summary><pre className="mt-2 max-h-72 overflow-auto rounded-lg bg-surface-3 p-3 text-[11px]">{JSON.stringify(report?.resolved_spec || spec, null, 2)}</pre></details>
      </details>
      {report && <div>
        <div className="mb-2 flex items-center gap-2"><h3 className="label mb-0">Checks</h3>
          {report.valid ? <Badge tone="good">Ready to mesh</Badge> : <Badge tone="bad">{failing.length} to fix</Badge>}
          {experimental && <ExperimentalBadge />}</div>
        {findings.length ? <ul className="space-y-1.5">{findings.map((f, i) => <FindingRow key={f.code + i} finding={f} />)}</ul> : <p className="hint">All checks pass.</p>}
      </div>}
      <div className="space-y-1 rounded-xl border border-line bg-surface-2 p-3">
        <CheckBox checked={s.attested.dims} onChange={(dims) => s.attest({ dims })}>I checked the model size, the flow direction, the selected parts and the point inside the fluid.</CheckBox>
        {spec.use_case?.kind !== 'pipe' && <CheckBox checked={s.attested.refs} onChange={(refs) => s.attest({ refs })}>The reference area and length are right for drag and lift coefficients (without this, forces are still reported).</CheckBox>}
        {s.reviewNeeded && <CheckBox checked={s.acknowledged} onChange={s.acknowledge}>I read the items marked “Review” above{experimental ? ', including the experimental physics,' : ''} and want to continue.</CheckBox>}
      </div>
      <div className="flex flex-wrap items-center gap-2" data-tour="launch">
        <button className="btn-primary px-5 py-2.5" disabled={!canLaunch} onClick={launch}><Rocket size={16} />{busy ? 'Starting…' : 'Launch simulation'}</button>
        <button className="btn-secondary" disabled={!canLaunch} onClick={() => setBatching(true)}><Layers3 size={15} />Launch a batch…</button>
        <button className="btn-secondary" disabled={!report?.valid} onClick={caseOnly}><Download size={15} />Download OpenFOAM case only</button>
        {!health.data?.worker.online && <span className="hint">The compute worker is offline: the simulation will wait in the queue.</span>}
      </div>
      {!canLaunch && report?.valid && <p className="hint">{!s.attested.dims ? 'Tick the confirmation above to launch.' : !s.acknowledged ? 'Acknowledge the review items to launch.' : ''}</p>}
      {batching && <BatchDialog s={s} onClose={() => setBatching(false)} onLaunched={(id) => navigate(`/batches/${id}`)} />}
      <p className="hint">The simulation meshes, checks the mesh (continuing automatically when the mesh has no findings), solves until the forces converge, then builds the results and the PDF report. Completed results are not experimentally validated.</p>
    </>}
  </Step>;
}

export default function SetupPage({ projectId }: { projectId: string }) {
  const s = useSetup(projectId);
  const theme = useViewerTheme();
  const [hidden, setHidden] = useState<string[]>([]);
  const [transform, setTransform] = useState<{ rotation: Vec3; scale: number }>({ rotation: [0, 0, 0], scale: 1 });
  const [showDomain, setShowDomain] = useState(false);
  const [dims, setDims] = useState<{ axis: number; length: number; x: number; y: number; visible: boolean }[]>([]);
  const geometry = s.geometry.data;
  const intent = s.intent;
  const spec: any = s.spec;
  const groupsOf = (names: string[]) => (geometry?.patches || []).filter((p) => names.includes(p.name)).map((p) => p.group);
  const matrix = useMemo(() => (transform.scale !== 1 || transform.rotation.some((v) => v) ? transformMatrix(transform.rotation, transform.scale) : null), [transform]);
  const units = usePreferences((u) => u.units);
  useEffect(() => {
    useAgentBridge.getState().publish({ projectId, geometry: geometry || null, spec: s.spec, report: s.report || null, setIntent: (change) => s.setIntent(change as any),
      attest: s.attest, acknowledge: s.acknowledge, launch: s.launch });
  });
  useEffect(() => () => useAgentBridge.getState().publish(null), []);

  if (s.project.isError) return <EmptyState title="This setup was not found"><Link href="/" className="btn-primary mt-2">Back to simulations</Link></EmptyState>;
  if (!s.workspace) return <div className="grid flex-1 place-items-center"><Spinner label="Opening the setup" /></div>;
  const flowVector = spec ? flowDirection(spec.flow.alpha_deg, spec.flow.beta_deg) : [1, 0, 0];
  const pick = (group: number) => {
    const patch = geometry?.patches.find((p) => p.group === group);
    if (!patch || !intent) return;
    s.setIntent({ patches: intent.patches.includes(patch.name) ? intent.patches.filter((n) => n !== patch.name) : [...intent.patches, patch.name] });
  };
  return <div className="mx-auto grid w-full max-w-[1800px] flex-1 gap-4 px-4 py-4 lg:grid-cols-[minmax(24rem,36rem)_1fr]">
    <div className="min-w-0 space-y-4 pb-24">
      <div className="flex items-center gap-2">
        <Link href="/" className="btn-ghost btn-icon" aria-label="Back to simulations"><ArrowLeft size={18} /></Link>
        <input className="input border-transparent bg-transparent text-lg font-semibold hover:border-line focus:bg-surface" value={s.name} onChange={(e) => s.setName(e.target.value)} aria-label="Simulation name" maxLength={120} />
        <span className="chip shrink-0">Draft · saved</span>
      </div>
      {!geometry || !intent ? <Spinner label="Loading the model" /> : <>
        <ModelSection s={s} hidden={hidden} setHidden={setHidden} transform={transform} setTransform={setTransform} />
        <TypeSection s={s} />
        <FlowSection s={s} />
        <ElementsSection s={s} />
        <AccuracySection s={s} />
        <ReviewSection s={s} />
      </>}
    </div>
    <div className="lg:sticky lg:top-[4.5rem] lg:h-[calc(100vh-5.5rem)]">
      <ViewerFrame className="h-[60vh] rounded-2xl border border-line lg:h-full" bounds={geometry?.bounds} flow={flowVector} lift={spec?.references.lift_axis}
        geometry={s.scene.data ? { scene: s.scene.data, selectedGroups: groupsOf(intent?.patches || []), hiddenGroups: groupsOf(hidden), onPick: pick, matrix,
          highlightGroups: groupsOf([...(s.overrides.elements?.wheels || []).filter((w) => w.rotating).flatMap((w) => w.patches), ...(s.overrides.elements?.rotating_zones || []).flatMap((z) => z.patches)]),
          colors: { neutral: theme.neutral, selected: intent?.kind === 'propeller' ? theme.highlight : theme.selected, highlight: theme.highlight } } : null}
        overlays={geometry && spec ? { flow: spec.flow, rotation: spec.rotation, domain: showDomain ? spec.domain : null, bounds: geometry.bounds, dimensions: true, color: [0.98, 0.45, 0.33],
          ground: spec.environment?.ground && spec.environment.ground !== 'none' ? { up: [0, 0, 1], offset: Math.max(intent?.ride_height_m || 0, 0) } : null,
          boxes: (spec.elements?.porous || []).map((p: any) => ({ center: p.center, axis: p.axis, up: p.up, size: [p.thickness_m, p.width_m, p.height_m] })) } : null}
        onDimensionLabels={setDims}
        toolbar={<label className="flex items-center gap-2 rounded-xl border border-line bg-surface/90 px-2.5 py-1.5 text-xs font-semibold shadow-sm backdrop-blur"><input type="checkbox" checked={showDomain} onChange={(e) => setShowDomain(e.target.checked)} />Wind tunnel</label>}>
        {dims.filter((d) => d.visible).map((d) => <span key={d.axis} className="pointer-events-none absolute -translate-x-1/2 -translate-y-1/2 rounded-md bg-surface/90 px-1.5 py-0.5 text-[11px] font-semibold text-danger shadow-sm" style={{ left: d.x, top: d.y }}>{formatQuantity('length', d.length, units.length, 3)}</span>)}
        {geometry && <div className="pointer-events-none absolute bottom-3 right-3 rounded-xl border border-line bg-surface/90 px-3 py-2 text-xs shadow-sm backdrop-blur">
          <div className="eyebrow mb-0.5">Model dimensions</div>
          {['X', 'Y', 'Z'].map((axis, i) => <div key={axis} className="flex justify-between gap-4 tabular-nums"><span className={['text-axis-x', 'text-axis-y', 'text-axis-z'][i] + ' font-bold'}>{axis}</span><span>{formatQuantity('length', geometry.dimensions[i] * transform.scale, units.length, 4)}</span></div>)}
        </div>}
        {spec && intent?.kind !== 'pipe' && <div className="pointer-events-none absolute bottom-3 left-28 rounded-lg bg-surface/90 px-2.5 py-1 text-xs font-medium text-ink-2 shadow-sm">
          {intent?.kind === 'propeller' ? `${significant(intent.rpm, 5)} RPM · axis and spin shown` : `${formatQuantity('speed', spec.flow.speed, units.speed, 3)} · arrows show the incoming flow`}
        </div>}
      </ViewerFrame>
    </div>
  </div>;
}

