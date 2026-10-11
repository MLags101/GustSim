import { useEffect, useState } from 'react';
import { CircleDot, Fan, Plus, Spline, Trash2, Waves, Wind } from 'lucide-react';
import { post } from '../../api/client';
import type { Vec3 } from '../../api/types';
import { significant } from '../../lib/units';
import { ExperimentalBadge, NumberField, Segmented, SelectField, Spinner, Switch, VectorField, cx, toast } from '../../ui';
import type { DesignBoxDraft, ElementsDraft, OpeningElement, PorousElement, SetupState, ZoneElement } from './useSetup';

function ElementCard({ icon, title, children, actions }: { icon: React.ReactNode; title: string; children: React.ReactNode; actions?: React.ReactNode }) {
  return <section aria-label={title} className="rounded-xl border border-line p-3.5">
    <div className="mb-2 flex items-center gap-2"><span className="text-accent">{icon}</span><h3 className="text-sm font-semibold">{title}</h3><ExperimentalBadge /><span className="ml-auto flex gap-1">{actions}</span></div>
    <div className="space-y-3">{children}</div>
  </section>;
}

function useElements(s: SetupState) {
  const elements: ElementsDraft = s.overrides.elements || {};
  const set = (change: Partial<ElementsDraft>) => s.setOverrides({ elements: { ...elements, ...change } });
  return [elements, set] as const;
}

export function WheelsEditor({ s }: { s: SetupState }) {
  const [elements, set] = useElements(s);
  const [busy, setBusy] = useState(false);
  const geometry = s.geometry.data!;
  const intent = s.intent!;
  const wheels = elements.wheels || [];
  async function detect() {
    setBusy(true);
    try {
      const travel = intent.direction;
      const found = await post<any>(`/geometry/${geometry.id}/wheels/detect`, { forward: travel, up: [0, 0, 1] });
      if (!found.candidates.length) { toast.info('No wheel-shaped parts found. Wheels need to be separate parts (bodies) in the model.'); return; }
      const detected = found.candidates.map((c: any, i: number) => ({ name: `wheel${i + 1}`, patches: c.patches, center: c.center, axis: c.axis, radius: c.radius, rotating: true }));
      s.setOverrides({ elements: { ...elements, wheels: detected }, front_axle: found.front_axle || null, rear_axle: found.rear_axle || null });
      toast.success(`${found.candidates.length} wheels found${found.wheelbase_m ? `, wheelbase ${significant(found.wheelbase_m, 3)} m` : ''}.`);
    } catch (e) { toast.error(e); } finally { setBusy(false); }
  }
  return <ElementCard icon={<CircleDot size={16} />} title="Rotating wheels" actions={<button className="btn-secondary btn-sm" disabled={busy || intent.ground === 'none' || !intent.ground} onClick={detect}>{busy ? <Spinner label="Detecting" /> : 'Detect wheels'}</button>}>
    {(intent.ground || 'none') === 'none' ? <p className="hint">Choose “On ground” under Simulation type to add wheels.</p>
      : !wheels.length ? <p className="hint">Wheels spin so the tyre contact moves with the ground. Detection looks for round, flat parts with a sideways axle touching the ground.</p>
      : <ul className="space-y-1.5">{wheels.map((w, i) => <li key={w.name} className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={w.rotating} aria-label={`${w.name} rotates`} onChange={(e) => set({ wheels: wheels.map((x, j) => (j === i ? { ...x, rotating: e.target.checked } : x)) })} />
        <span className="font-medium">{w.name}</span><span className="text-muted">r = {significant(w.radius, 3)} m · {w.patches.length} surfaces · at ({w.center.map((v) => significant(v, 3)).join(', ')})</span>
        <button className="ml-auto text-muted hover:text-danger" aria-label={`Remove ${w.name}`} onClick={() => set({ wheels: wheels.filter((_, j) => j !== i) })}><Trash2 size={14} /></button>
      </li>)}</ul>}
    {s.overrides.front_axle && <p className="hint">Front and rear axles found: lift is split between them in the results.</p>}
  </ElementCard>;
}

// A typical car radiator curve; the box starts at a size proportional to the model so it is visible and editable.
const DEFAULT_RADIATOR = (center: Vec3, size: number): PorousElement => ({ name: 'radiator', center, axis: [1, 0, 0], up: [0, 0, 1], width_m: round(0.25 * size), height_m: round(0.18 * size), thickness_m: round(0.025 * size), curve: [[5, 330], [7, 556]], transverse_factor: 10 });
const round = (v: number) => Number(v.toPrecision(2));

export function RadiatorsEditor({ s }: { s: SetupState }) {
  const [elements, set] = useElements(s);
  const list = elements.porous || [];
  const geometry = s.geometry.data!;
  const [fit, setFit] = useState<Record<string, any>>({});
  const update = (i: number, change: Partial<PorousElement>) => set({ porous: list.map((x, j) => (j === i ? { ...x, ...change } : x)) });
  const key = JSON.stringify(list.map((p) => [p.curve, p.thickness_m]));
  useEffect(() => {
    let alive = true;
    Promise.all(list.map((p) => post('/porous/fit', { curve: p.curve, thickness_m: p.thickness_m, density: (s.spec as any)?.fluid.density, dynamic_viscosity: (s.spec as any)?.fluid.dynamic_viscosity }).catch((e) => ({ error: String(e.message || e) }))))
      .then((results) => { if (alive) setFit(Object.fromEntries(list.map((p, i) => [p.name, results[i]]))); });
    return () => { alive = false; };
  }, [key]);
  const center = geometry.bounds[0].map((v, i) => (v + geometry.bounds[1][i]) / 2) as Vec3;
  const size = Math.max(...geometry.bounds[1].map((v, i) => v - geometry.bounds[0][i]));
  return <ElementCard icon={<Waves size={16} />} title="Radiators (porous zones)" actions={<button className="btn-secondary btn-sm" onClick={() => set({ porous: [...list, { ...DEFAULT_RADIATOR(center, size), name: `radiator${list.length + 1}` }] })}><Plus size={13} />Add</button>}>
    {!list.length && <p className="hint">A radiator resists the flow like a heat exchanger. Give two measured points (air speed through it, pressure drop) and its box.</p>}
    {list.map((p, i) => <div key={i} className="space-y-3 rounded-lg bg-surface-2 p-3">
      <div className="flex items-center gap-2"><input className="input py-1 text-sm" aria-label="Radiator name" value={p.name} onChange={(e) => update(i, { name: e.target.value.replace(/[^A-Za-z0-9_]/g, '') || 'radiator' })} />
        <button className="text-muted hover:text-danger" aria-label="Remove radiator" onClick={() => set({ porous: list.filter((_, j) => j !== i) })}><Trash2 size={14} /></button></div>
      <VectorField label="Centre · m" value={p.center} onChange={(center) => update(i, { center })} />
      <VectorField label="Flow-through direction" value={p.axis} onChange={(axis) => update(i, { axis })} />
      <div className="grid grid-cols-3 gap-2">
        <NumberField label="Width · m" value={p.width_m} onChange={(width_m) => update(i, { width_m })} />
        <NumberField label="Height · m" value={p.height_m} onChange={(height_m) => update(i, { height_m })} />
        <NumberField label="Depth · m" value={p.thickness_m} onChange={(thickness_m) => update(i, { thickness_m })} />
      </div>
      <div className="grid grid-cols-2 gap-2">{[0, 1].map((k) => <div key={k} className="grid grid-cols-2 gap-2">
        <NumberField label={`Speed ${k + 1} · m/s`} value={p.curve[k][0]} onChange={(v) => update(i, { curve: p.curve.map((c, j) => (j === k ? [v, c[1]] : c)) as PorousElement['curve'] })} />
        <NumberField label={`Δp ${k + 1} · Pa`} value={p.curve[k][1]} onChange={(v) => update(i, { curve: p.curve.map((c, j) => (j === k ? [c[0], v] : c)) as PorousElement['curve'] })} />
      </div>)}</div>
      {fit[p.name]?.error ? <p className="text-xs text-danger">{fit[p.name].error}</p>
        : fit[p.name] ? <p className="hint">Fitted Darcy–Forchheimer: d = {significant(fit[p.name].d, 3)} 1/m², f = {significant(fit[p.name].f, 3)} 1/m (Δp = {significant(fit[p.name].a, 3)}·v + {significant(fit[p.name].b, 3)}·v²).</p> : null}
    </div>)}
  </ElementCard>;
}

const OPENING_KINDS: { value: OpeningElement['kind']; label: string; unit: string }[] = [
  { value: 'outflow_flow_rate', label: 'Air leaves here (flow rate)', unit: 'm³/s' }, { value: 'outflow_pressure', label: 'Air leaves here (pressure)', unit: 'Pa' },
  { value: 'inflow_velocity', label: 'Air enters here (speed)', unit: 'm/s' }, { value: 'inflow_flow_rate', label: 'Air enters here (flow rate)', unit: 'm³/s' },
  { value: 'total_pressure', label: 'Air enters here (total pressure)', unit: 'Pa' },
];

export function OpeningsEditor({ s }: { s: SetupState }) {
  const [elements, set] = useElements(s);
  const list = elements.openings || [];
  const patches = s.geometry.data!.patches.map((p) => p.name);
  const free = patches.filter((p) => !list.some((o) => o.patch === p));
  const update = (i: number, change: Partial<OpeningElement>) => set({ openings: list.map((x, j) => (j === i ? { ...x, ...change } : x)) });
  return <ElementCard icon={<Wind size={16} />} title="Flow openings" actions={<button className="btn-secondary btn-sm" disabled={!free.length} onClick={() => set({ openings: [...list, { patch: free[0], kind: 'outflow_flow_rate', value: 0.05 }] })}><Plus size={13} />Add</button>}>
    {!list.length && <p className="hint">Intakes, grilles and exhausts: a surface of the model where air leaves or enters with a set flow, speed or pressure. Openings are excluded from the measured walls.</p>}
    {list.map((o, i) => { const unit = OPENING_KINDS.find((k) => k.value === o.kind)?.unit; return <div key={i} className="grid grid-cols-[1fr_1fr_7rem_auto] items-end gap-2">
      <SelectField label="Surface" value={o.patch} onChange={(patch) => update(i, { patch })} options={[o.patch, ...free].map((p) => ({ value: p, label: p }))} />
      <SelectField label="Condition" value={o.kind} onChange={(kind) => update(i, { kind })} options={OPENING_KINDS.map(({ value, label }) => ({ value, label }))} />
      <NumberField label={unit || ''} value={o.value} onChange={(value) => update(i, { value })} />
      <button className="mb-2 text-muted hover:text-danger" aria-label="Remove opening" onClick={() => set({ openings: list.filter((_, j) => j !== i) })}><Trash2 size={14} /></button>
    </div>; })}
  </ElementCard>;
}

export function RotorsEditor({ s }: { s: SetupState }) {
  const [elements, set] = useElements(s);
  const list = elements.rotating_zones || [];
  const intent = s.intent!;
  const geometry = s.geometry.data!;
  const [patches, setPatches] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const claimed = new Set([...intent.patches, ...list.flatMap((z) => z.patches)]);
  async function add() {
    if (!patches.length) { toast.info('Tick the surfaces of the additional rotor first.'); return; }
    setBusy(true);
    try {
      const fit = await post<any>(`/geometry/${geometry.id}/rotor-suggestion`, { patches, origin: intent.origin, axis: intent.axis });
      const zone: ZoneElement = { name: `rotor${list.length + 2}`, origin: fit.origin, axis: fit.axis, rpm: intent.rpm, radius: fit.radius, length: fit.length, blade_radius: fit.blade_radius, patches };
      set({ rotating_zones: [...list, zone] }); setPatches([]);
    } catch (e) { toast.error(e); } finally { setBusy(false); }
  }
  const update = (i: number, change: Partial<ZoneElement>) => set({ rotating_zones: list.map((x, j) => (j === i ? { ...x, ...change } : x)) });
  return <ElementCard icon={<Fan size={16} />} title="Additional rotors">
    {list.map((z, i) => <div key={i} className="space-y-2 rounded-lg bg-surface-2 p-3">
      <div className="flex items-center gap-2 text-sm"><span className="font-semibold">{z.name}</span><span className="text-muted">{z.patches.length} surfaces · region r {significant(z.radius, 3)} m</span>
        <button className="ml-auto text-muted hover:text-danger" aria-label={`Remove ${z.name}`} onClick={() => set({ rotating_zones: list.filter((_, j) => j !== i) })}><Trash2 size={14} /></button></div>
      <div className="grid grid-cols-2 gap-2"><NumberField label="RPM (signed)" value={z.rpm} onChange={(rpm) => update(i, { rpm })} /><VectorField label="Axis" value={z.axis} onChange={(axis) => update(i, { axis })} /></div>
      <VectorField label="Centre · m" value={z.origin} onChange={(origin) => update(i, { origin })} />
    </div>)}
    <details><summary className="cursor-pointer text-sm font-semibold text-ink-2">Add another rotor</summary>
      <p className="hint my-2">Tick its surfaces (not already rotating), then fit a region around them. Rotating regions must not overlap.</p>
      <div className="max-h-40 space-y-0.5 overflow-y-auto">{geometry.patches.filter((p) => !claimed.has(p.name)).map((p) => <label key={p.name} className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={patches.includes(p.name)} onChange={() => setPatches((v) => (v.includes(p.name) ? v.filter((x) => x !== p.name) : [...v, p.name]))} />{p.name}</label>)}</div>
      <button className={cx('btn-secondary btn-sm mt-2')} disabled={busy} onClick={add}><Plus size={13} />Fit region and add</button>
    </details>
  </ElementCard>;
}

export function DesignEditor({ s }: { s: SetupState }) {
  const [elements, set] = useElements(s);
  const geometry = s.geometry.data!;
  const design = s.overrides.design || { enabled: false, objective: 'drag' as const, iterations: 400, cycles: 8 };
  const boxes = elements.design_boxes || [];
  const setDesign = (change: Partial<typeof design>) => s.setOverrides({ design: { ...design, ...change } });
  const setBoxes = (design_boxes: DesignBoxDraft[]) => {
    set({ design_boxes });
    if (design_boxes.length && !design.enabled) setDesign({ enabled: true });
  };
  function addBox() {
    const bounds = geometry.bounds;
    const center = bounds[0].map((v, i) => (v + bounds[1][i]) / 2) as Vec3;
    const size = Math.max(...bounds[0].map((v, i) => bounds[1][i] - v)) * 0.35 || 0.1;
    setBoxes([...boxes, { name: `box${boxes.length + 1}`, center, dimensions: [size, size, size], max_displacement_m: Math.max(size * 0.02, 0.001) }]);
  }
  return <ElementCard icon={<Spline size={16} />} title="Design advice" actions={<Switch checked={design.enabled} onChange={(enabled) => setDesign({ enabled })} label="On" />}>
    <p className="hint">Adjoint sensitivity of drag or lift. Blue means move the surface inward to reduce it; red means move it outward. With a design box, morphing runs afterwards as its own attempt and queues a check simulation of the moved shape. Both are experimental and are not a measured improvement.</p>
    {design.enabled && <>
      <Segmented label="Objective" value={design.objective} onChange={(objective) => setDesign({ objective })} options={[{ value: 'drag', label: 'Drag' }, { value: 'lift', label: 'Lift' }]} />
      <div className="grid grid-cols-2 gap-2">
        <NumberField label="Adjoint iterations" value={design.iterations} min={50} max={2000} step={50} onChange={(iterations) => setDesign({ iterations })} />
        <NumberField label="Morphing cycles" value={design.cycles} min={1} max={20} step={1} onChange={(cycles) => setDesign({ cycles })} hint="Used only when a design box is set. Stops early when the objective rises." />
      </div>
      {boxes.map((box, i) => <div key={i} className="space-y-2 rounded-lg bg-surface-2 p-3">
        <div className="flex items-center gap-2 text-sm"><span className="font-semibold">{box.name}</span>
          <button className="ml-auto text-muted hover:text-danger" aria-label={`Remove ${box.name}`} onClick={() => setBoxes(boxes.filter((_, j) => j !== i))}><Trash2 size={14} /></button></div>
        <VectorField label="Centre · m" value={box.center} onChange={(center) => setBoxes(boxes.map((b, j) => (j === i ? { ...b, center } : b)))} />
        <VectorField label="Size · m" value={box.dimensions} onChange={(dimensions) => setBoxes(boxes.map((b, j) => (j === i ? { ...b, dimensions } : b)))} />
        <NumberField label="Maximum move · m" value={box.max_displacement_m} min={0.0001} step={0.001} onChange={(max_displacement_m) => setBoxes(boxes.map((b, j) => (j === i ? { ...b, max_displacement_m } : b)))} />
      </div>)}
      <button className="btn-secondary btn-sm" onClick={addBox}><Plus size={13} />Add a design box</button>
      {!boxes.length && <p className="hint">No design box: the run still writes the sensitivity map, and does not move the shape.</p>}
    </>}
  </ElementCard>;
}
