import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { PanelRightClose, PanelRightOpen, Pause, Play, Wind } from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { post } from '../../api/client';
import { create } from 'zustand';
import type { ArrayInfo, Forces, Layer, Manifest, Run } from '../../api/types';
import { COLORMAPS, type ColormapId } from '../../lib/colormaps';
import { formatQuantity, significant } from '../../lib/units';
import { usePreferences } from '../../state/preferences';
import { EmptyState, FloatCard, InfoTip, Pills, Segmented, Switch, cx, toast } from '../../ui';
import { prefetch } from '../../viewer/data';
import { Legend } from '../../viewer/Legend';
import { ViewerFrame } from '../../viewer/ViewerFrame';
import type { ProbeResult, ViewLayer, ViewportHandle } from '../../viewer/Viewport';
import { useViewerTheme } from '../../viewer/theme';
import { annotateScreenshot } from './screenshot';
import { isShare, shareFile } from '../../share/bundle';

export type VolumeMode = 'none' | 'streamlines' | 'cloud' | 'vortex' | 'noise' | 'section' | 'water';
export interface FlowState {
  field: string; colormap: ColormapId; rangeMode: 'auto' | 'symmetric' | 'fixed' | 'default'; fixed: [number, number];
  friction: boolean; objectOpacity: number; probe: boolean;
  volume: VolumeMode; cloudOpacity: number; vortex: string | null; noise: string | null;
  /** Streamlines: orientation, which seed plane (null = all), line density 1–20, particles. */
  rakes: 'horizontal' | 'vertical'; plane: number | null; lines: number; animate: boolean; particles: number;
  section: 'x' | 'y' | 'z'; sectionIndex: number; sectionField: string;
}

export const ARRAY_DEFAULT_MAP: Record<string, ColormapId> = { Cp: 'cool_to_warm', pressure_pa: 'cool_to_warm', Cf: 'rainbow', wall_shear_mag: 'rainbow', U_near_mag: 'rainbow', yPlus: 'viridis', U_mag: 'rainbow', Cpt: 'rainbow', sensitivity: 'advice', elevation_m: 'cool_to_warm', alpha_water: 'cool_to_warm' };
export const FIELD_NAMES: Record<string, string> = { Cp: 'Pressure (Cp)', pressure_pa: 'Pressure', Cf: 'Friction (Cf)', wall_shear_mag: 'Wall shear', U_near_mag: 'Near-wall velocity', yPlus: 'y+', U_mag: 'Velocity', Cpt: 'Total pressure (Cpt)', sensitivity: 'Design advice', elevation_m: 'Elevation', alpha_water: 'Water fraction' };
// Surface pills in AirShaper order; anything else the package wrote follows.
const SURFACE_ORDER = ['Cp', 'Cf', 'U_near_mag', 'wall_shear_mag', 'pressure_pa', 'yPlus', 'sensitivity', 'alpha_water'];
const PILL_NAMES: Record<string, string> = { Cp: 'Pressure', Cf: 'Friction', U_near_mag: 'Velocity', wall_shear_mag: 'Wall shear', pressure_pa: 'Pressure (Pa)', yPlus: 'y+', sensitivity: 'Design advice', alpha_water: 'Wetted' };
const VOLUME_NAMES: Record<VolumeMode, string> = { none: 'None', streamlines: 'Streamlines', cloud: 'Loss clouds', vortex: 'Vortices', noise: 'Noise', section: 'Slices', water: 'Water surface' };
const VOLUME_MISSING: Partial<Record<VolumeMode, string>> = { streamlines: 'rakes', cloud: 'pressure_cloud', vortex: 'q_criterion', noise: 'noise_cloud', section: 'sections', water: 'free_surface' };

const layersOf = (manifest?: Manifest | null) => manifest?.layers || [];
const rakesOf = (manifest: Manifest | null | undefined, orientation: string) => layersOf(manifest).filter((l) => l.kind === 'streamlines' && l.orientation === orientation).sort((a, b) => (a.offset ?? 0) - (b.offset ?? 0));

export const defaultFlowState = (manifest?: Manifest | null): FlowState => {
  const surface = layersOf(manifest).find((l) => l.id === 'surface');
  const field = surface?.arrays.some((a) => a.name === 'Cp') ? 'Cp' : surface?.arrays.find((a) => !a.categorical)?.name || 'pressure_pa';
  const q = layersOf(manifest).filter((l) => l.id.startsWith('q_'));
  const noise = layersOf(manifest).filter((l) => l.id.startsWith('noise_'));
  const axis = (['x', 'y', 'z'] as const).find((a) => layersOf(manifest).some((l) => l.kind === 'section_stack' && l.axis === a)) || 'x';
  return { field, colormap: ARRAY_DEFAULT_MAP[field] || 'cool_to_warm', rangeMode: 'default', fixed: [-1, 1], friction: false, objectOpacity: 1, probe: true,
    volume: 'none', cloudOpacity: 0.55, vortex: q[Math.floor(q.length / 2)]?.id || null, noise: noise[0]?.id || null,
    rakes: rakesOf(manifest, 'horizontal').length || !rakesOf(manifest, 'vertical').length ? 'horizontal' : 'vertical', plane: -1, lines: 17, animate: false, particles: 12,
    section: axis, sectionIndex: -1, sectionField: 'U_mag' };
};

/** View settings per run survive switching results sections. */
const useFlowStore = create<{ byRun: Record<string, FlowState>; save: (runId: string, state: FlowState) => void }>((set) => ({
  byRun: {}, save: (runId, state) => set((s) => ({ byRun: { ...s.byRun, [runId]: state } })),
}));

function rangeFor(info: ArrayInfo | undefined, state: FlowState, field: string): [number, number] | null {
  if (!info?.range) return null;
  if (state.rangeMode === 'fixed') return state.fixed;
  if (state.rangeMode === 'symmetric') { const m = Math.max(Math.abs(info.range[0]), Math.abs(info.range[1])); return [-m, m]; }
  if (state.rangeMode === 'default' && info.default_range) return info.default_range as [number, number];
  if (state.rangeMode === 'default' && field === 'Cp') return [-1, 1];
  if (state.rangeMode === 'default' && info.diverging && !info.default_range) {
    const span = Math.max(Math.abs(info.range[0]), Math.abs(info.range[1]));
    return [-span, span];
  }
  return info.range;
}

/** One colour range over several layers (all rakes share a scale, so colours compare). */
function unionRange(layers: Layer[], name: string): [number, number] | null {
  const ranges = layers.map((l) => l.arrays.find((a) => a.name === name)?.range).filter(Boolean) as [number, number][];
  return ranges.length ? [Math.min(...ranges.map((r) => r[0])), Math.max(...ranges.map((r) => r[1]))] : null;
}

async function loadFrames(url: string): Promise<Float32Array> {
  if (isShare) {
    const embedded = shareFile(url);
    if (!embedded) throw new Error('Animation frames are not included in this shared report');
    return new Float32Array(embedded);
  }
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Animation frames: HTTP ${response.status}`);
  return new Float32Array(await response.arrayBuffer());
}

function Slider({ label, value, min, max, step = 1, onChange, display }: { label: string; value: number; min: number; max: number; step?: number; onChange: (v: number) => void; display?: ReactNode }) {
  return <label className="block text-xs text-muted">
    <span className="flex justify-between gap-2"><span>{label}</span>{display !== undefined && <span className="tabular-nums text-ink-2">{display}</span>}</span>
    <input type="range" className="mt-1 w-full accent-[var(--accent)]" min={min} max={max} step={step} value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))} />
  </label>;
}

function Toggle({ label, checked, onChange }: { label: string; checked: boolean; onChange: (v: boolean) => void }) {
  return <label className="flex items-center justify-between gap-2 text-sm text-ink-2">{label}<Switch checked={checked} onChange={onChange} label={label} /></label>;
}

function Stat({ label, value, help }: { label: string; value: string; help?: string }) {
  return <div className="min-w-0">
    <div className="flex items-center gap-1 text-[11px] font-medium text-muted">{label}{help && <InfoTip content={help} />}</div>
    <div className="truncate text-base font-semibold tabular-nums text-ink">{value}</div>
  </div>;
}

function ResultsCard({ run, forces }: { run: Run; forces: Forces | null }) {
  const units = usePreferences((s) => s.units);
  const m = run.result?.metrics || {};
  const c = forces?.coefficients || {};
  const drag = forces?.frames.wind.total.force_n.drag ?? m.drag_n;
  const lift = forces?.frames.wind.total.force_n.lift ?? m.lift_n;
  const stats: [string, string, string?][] = [];
  if (m.thrust_n != null) stats.push(['Thrust', formatQuantity('force', m.thrust_n, units.force)]);
  if (m.torque_nm != null) stats.push(['Torque', formatQuantity('moment', m.torque_nm, units.moment)]);
  if (m.pressure_drop_pa != null) stats.push(['Pressure drop', formatQuantity('pressure', m.pressure_drop_pa, units.pressure)]);
  const rotor = m.thrust_n != null;
  if (rotor && m.efficiency != null) stats.push(['Efficiency η', `${(m.efficiency * 100).toFixed(1)} %`, 'Propulsive efficiency T·V / (2π n Q).']);
  if (rotor && m.shaft_power_w != null) stats.push(['Shaft power', formatQuantity('power', m.shaft_power_w, units.power)]);
  // Drag and lift coefficients of a rotor's body force have no meaning; rotors report thrust and torque.
  const cd = rotor ? null : c.cd ?? m.cd, cl = rotor ? null : c.cl ?? m.cl;
  if (cd != null) stats.push(['Cd', significant(cd, 3), 'Drag coefficient, averaged over the converged window.']);
  if (!rotor && c.cda_m2 != null) stats.push(['CdA', `${significant(c.cda_m2, 3)} m²`, 'Drag area: drag / (½ρV²).']);
  if (cl != null) stats.push(['Cl', significant(cl, 3)]);
  if (drag != null && m.thrust_n == null) stats.push(['Drag', formatQuantity('force', drag, units.force)]);
  if (lift != null && m.thrust_n == null && m.pressure_drop_pa == null) stats.push(['Lift', formatQuantity('force', lift, units.force)]);
  if (!stats.length) return null;
  return <FloatCard title="Results">
    <div className="grid grid-cols-2 gap-x-3 gap-y-2">{stats.slice(0, 6).map(([label, value, help]) => <Stat key={label} label={label} value={value} help={help} />)}</div>
  </FloatCard>;
}

export function FlowTab({ run, manifest, legacy, forces = null }: { run: Run; manifest: Manifest | null; legacy: boolean; forces?: Forces | null }) {
  const stored = useFlowStore((s) => s.byRun[run.id]);
  const save = useFlowStore((s) => s.save);
  const state = stored || defaultFlowState(manifest);
  const set = (change: Partial<FlowState>) => save(run.id, { ...state, ...change });
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [panel, setPanel] = useState(() => typeof window === 'undefined' || window.innerWidth >= 900);
  const viewer = useRef<ViewportHandle | null>(null);
  const client = useQueryClient();
  const [building, setBuilding] = useState(false);
  async function buildPackage() {
    setBuilding(true);
    try {
      await post(`/runs/${run.id}/package`);
      toast.info('The results package is being built; the 3D results appear when it finishes.');
      client.invalidateQueries({ queryKey: ['simulation'] });
      client.invalidateQueries({ queryKey: ['run', run.id] });
    } catch (e) { toast.error(e); setBuilding(false); }
  }
  const theme = useViewerTheme();
  // Transient animation: which frame layer is shown, and playback. Kept outside FlowState so a
  // frame step swaps values in place instead of rebuilding the layer list.
  const [animation, setAnimation] = useState<string | null>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  useEffect(() => { setAnimation(null); setFrame(0); setPlaying(false); }, [manifest?.run_id]);

  const all = layersOf(manifest);
  const layers: Record<string, Layer> = Object.fromEntries(all.map((l) => [l.id, l]));
  const surface = layers.surface;
  // Point and cell copies of the same field appear once (point data is used for display).
  const surfaceArrays = (surface?.arrays || []).filter((a, i, list) => !a.categorical && a.components === 1 && list.findIndex((b) => b.name === a.name && b.components === 1) === i)
    .sort((a, b) => (SURFACE_ORDER.indexOf(a.name) + 1 || 99) - (SURFACE_ORDER.indexOf(b.name) + 1 || 99));
  const surfaceInfo = surfaceArrays.find((a) => a.name === state.field);
  const q = all.filter((l) => l.id.startsWith('q_'));
  const noise = all.filter((l) => l.id.startsWith('noise_'));
  const rakes = rakesOf(manifest, state.rakes);
  const plane = state.plane === null ? null : rakes.length ? Math.min(state.plane < 0 ? Math.floor(rakes.length / 2) : state.plane, rakes.length - 1) : null;
  const shownRakes = state.volume !== 'streamlines' ? [] : plane === null ? rakes : rakes.slice(plane, plane + 1);
  const stacks = all.filter((l) => l.kind === 'section_stack');
  const stack = state.volume === 'section' ? stacks.find((s) => s.axis === state.section) || stacks[0] : undefined;
  const sectionIndex = stack?.items ? Math.min(state.sectionIndex < 0 ? Math.floor(stack.items.length / 2) : state.sectionIndex, stack.items.length - 1) : 0;
  const sectionItem = stack?.items?.[sectionIndex];
  const base = (path: string) => `/api/runs/${run.id}/files/${path}`;
  const animations = all.filter((l) => l.kind === 'animation' && l.frames);
  const animated = animations.find((l) => l.id === animation) || null;
  const frameData = useQuery({ queryKey: ['frames', run.id, animated?.frames?.url], enabled: !!animated?.frames,
    queryFn: () => loadFrames(base(animated!.frames!.url)), staleTime: Infinity });
  const frameCount = animated?.frames?.count || 0;
  useEffect(() => {
    if (!playing || !frameCount) return;
    const timer = window.setInterval(() => setFrame((f) => (f + 1) % frameCount), 1000/(8*speed));
    return () => window.clearInterval(timer);
  }, [playing, frameCount, speed]);
  const animationRange: [number, number] = animated?.frames?.array === 'Cp' ? [-1, 1] : (animated?.frames?.range || [0, 1]);
  const frames = useMemo(() => {
    const data = frameData.data, info = animated?.frames;
    if (!data || !info || data.length < info.count*info.values_per_frame) return null;
    const i = Math.min(frame, info.count - 1)*info.values_per_frame;
    return { animation: { field: info.array, values: data.subarray(i, i + info.values_per_frame) } };
  }, [frameData.data, frame, animated?.id]);

  useEffect(() => {
    if (!stack?.items) return;
    prefetch([stack.items[sectionIndex - 1], stack.items[sectionIndex + 1], stack.items[sectionIndex + 2]].filter(Boolean).map((item) => base(item!.url)));
  }, [stack, sectionIndex]);
  // A probe readout belongs to the view it was taken in.
  useEffect(() => setProbe(null), [state.probe, state.volume, state.field, sectionItem?.url, animated?.id]);

  const range = rangeFor(surfaceInfo, state, state.field) || [0, 1];
  const sectionInfo = stack?.arrays.find((a) => a.name === state.sectionField);
  const sectionRange: [number, number] = state.sectionField === 'Cp' ? [-1, 1] : (sectionInfo?.range as [number, number]) || [0, 1];
  const rakeRange = unionRange(rakes, 'U_mag') || [0, Math.max(1, manifest?.reference.speed_m_s || 1)*1.3];
  const vortex = state.volume === 'vortex' ? q.find((l) => l.id === state.vortex) || q[0] : undefined;
  const vortexRange = vortex ? unionRange([vortex], 'U_mag') : null;
  const noiseLayer = state.volume === 'noise' ? noise.find((l) => l.id === state.noise) || noise[0] : undefined;
  const water = state.volume === 'water' ? layers.free_surface : undefined;
  const waterRange = water ? unionRange([water], 'elevation_m') : null;
  const subsample = 21 - Math.min(20, Math.max(1, state.lines));

  const viewLayers: ViewLayer[] = useMemo(() => {
    if (legacy || !manifest) return [{ id: 'legacy', url: base('results/surface.vtp'), field: 'pressure_pa', colormap: state.colormap, range: null, pickable: true }];
    const out: ViewLayer[] = [];
    if (surface?.url) out.push({ id: 'surface', url: base(surface.url), field: state.field, colormap: state.colormap, range, opacity: state.objectOpacity, pickable: true });
    if (state.friction && layers.friction_lines?.url) out.push({ id: 'friction', url: base(layers.friction_lines.url), color: theme.dark ? [0.95, 0.97, 1] : [0.12, 0.16, 0.22], lineWidth: 1, pickable: false, ambient: 1 });
    if (state.volume === 'cloud' && layers.pressure_cloud?.url) out.push({ id: 'cloud', url: base(layers.pressure_cloud.url), color: [0.86, 0.15, 0.2], opacity: state.cloudOpacity, pickable: false });
    if (vortex?.url) out.push({ id: 'vortex', url: base(vortex.url), field: 'U_mag', colormap: 'rainbow', range: vortexRange, opacity: 0.85, pickable: false });
    if (noiseLayer?.url) out.push({ id: 'noise', url: base(noiseLayer.url), color: [0.85, 0.11, 0.38], opacity: 0.35, pickable: false });
    if (water?.url) out.push({ id: 'water', url: base(water.url), field: 'elevation_m', colormap: 'cool_to_warm', range: waterRange, opacity: 0.9, pickable: true });
    for (const rake of shownRakes) out.push({ id: rake.id, url: base(rake.url!), field: 'U_mag', colormap: 'rainbow', range: rakeRange, subsample, lineWidth: 1.5, pickable: false });
    if (sectionItem) out.push({ id: 'section', url: base(sectionItem.url), field: state.sectionField, colormap: ARRAY_DEFAULT_MAP[state.sectionField] || 'rainbow', range: sectionRange, pickable: true, opacity: 0.95 });
    if (animated?.url && animated.frames) {
      // The animated object surface replaces the time-averaged one once it has loaded; a section adds to it.
      const onObject = animated.id === 'animation_object';
      out.push({ id: 'animation', url: base(animated.url), field: animated.frames.array, colormap: onObject ? 'cool_to_warm' : 'rainbow',
        range: animationRange, pickable: true, opacity: onObject ? state.objectOpacity : 0.95, replaces: onObject ? 'surface' : undefined });
    }
    return out;
  }, [manifest, legacy, state, range[0], range[1], theme.dark, sectionItem?.url, animated?.id, shownRakes.map((r) => r.id).join(), subsample, vortex?.id, noiseLayer?.id, water?.id]);

  if (!manifest && !legacy) return <EmptyState icon={<Wind size={22} />} title="Results are being packaged">Surface fields, clouds, streamlines and sections appear when the results package completes.</EmptyState>;
  const fieldInfo = surfaceInfo || { name: state.field, unit: state.field === 'pressure_pa' ? 'Pa' : '1', label: FIELD_NAMES[state.field] } as ArrayInfo;
  const flow = manifest?.reference.frames.wind.drag;
  const lift = manifest?.reference.frames.wind.lift;
  const missing = (mode: VolumeMode) => manifest?.missing.find((m) => m.id === VOLUME_MISSING[mode])?.reason;
  const available: Record<VolumeMode, boolean> = { none: true, streamlines: all.some((l) => l.kind === 'streamlines'), cloud: !!layers.pressure_cloud, vortex: q.length > 0, noise: noise.length > 0, section: stacks.length > 0, water: !!layers.free_surface };
  const volumeOptions = (Object.keys(VOLUME_NAMES) as VolumeMode[]).filter((mode) => available[mode] || (mode !== 'water' && missing(mode)))
    .map((mode) => ({ value: mode, label: VOLUME_NAMES[mode], disabled: !available[mode], title: available[mode] ? undefined : missing(mode) }));
  const definition = manifest?.reference.definitions?.[state.field];
  const unitOf = (id: string) => {
    const unit = id === 'section' ? sectionInfo?.unit : id === 'animation' ? animated?.frames?.unit : id === 'water' ? 'm' : fieldInfo.unit;
    return unit && unit !== '1' ? unit : '';
  };
  const probeLayer = !state.probe ? null : animated?.frames ? { layerId: 'animation', field: animated.frames.array }
    : { layerId: sectionItem ? 'section' : legacy || !manifest ? 'legacy' : 'surface', field: sectionItem ? state.sectionField : state.field };
  const tracerLayers = state.animate && state.volume === 'streamlines' ? shownRakes.map((r) => r.id) : null;

  return <div className="absolute inset-0">
    <ViewerFrame ref={viewer} className="absolute inset-0" layers={viewLayers} bounds={manifest?.bounds.model} presets={manifest?.camera_presets} flow={flow} lift={lift}
      probeLayer={probeLayer} frames={frames} onProbe={setProbe} tracerLayers={tracerLayers} tracerDensity={state.particles}
      onLoading={setLoading} onError={setError} loading={loading} error={error}
      screenshotName={`${run.spec.name || 'gustsim'}-${state.field}.png`}
      onScreenshot={(image) => annotateScreenshot(image, { title: run.spec.name, subtitle: `${FIELD_NAMES[state.field] || state.field}`, colormap: state.colormap, range, unit: fieldInfo.unit || undefined, status: manifest?.status })}>
      <div className={cx('pointer-events-none absolute inset-x-0 bottom-3 flex flex-col items-center gap-2 px-3 transition-[padding] md:pl-[7.5rem]', panel && 'md:pr-[20rem]')}>
        <div className="flex flex-wrap items-end justify-center gap-2">
          {!(animated?.id === 'animation_object') && <Legend colormap={state.colormap} range={range} label={FIELD_NAMES[state.field] || fieldInfo.label || state.field} unit={fieldInfo.unit} />}
          {animated?.frames && <Legend colormap={animated.id === 'animation_object' ? 'cool_to_warm' : 'rainbow'} range={animationRange} label={`Instantaneous · ${FIELD_NAMES[animated.frames.array] || animated.frames.array}`} unit={animated.frames.unit || (animated.frames.array === 'Cp' ? '1' : undefined)} />}
          {shownRakes.length > 0 && <Legend colormap="rainbow" range={rakeRange} label="Streamline velocity" unit="m/s" />}
          {vortex && vortexRange && <Legend colormap="rainbow" range={vortexRange} label="Vortex velocity" unit="m/s" />}
          {sectionItem && <Legend colormap={ARRAY_DEFAULT_MAP[state.sectionField] || 'rainbow'} range={sectionRange} label={`Slice · ${FIELD_NAMES[state.sectionField] || state.sectionField}`} unit={sectionInfo?.unit} />}
          {water && waterRange && <Legend colormap="cool_to_warm" range={waterRange} label="Water surface elevation" unit="m" />}
        </div>
        {!legacy && manifest && <div className="pointer-events-auto flex max-w-full flex-wrap justify-center gap-2" data-tour="field-pills">
          {surfaceArrays.length > 0 && <Pills label="Surface" value={state.field} onChange={(field) => set({ field, colormap: ARRAY_DEFAULT_MAP[field] || 'rainbow', rangeMode: 'default' })}
            options={surfaceArrays.map((a) => ({ value: a.name, label: PILL_NAMES[a.name] || a.label || a.name, title: FIELD_NAMES[a.name] || a.label }))} />}
          <Pills label="Volume" value={state.volume} onChange={(volume) => set({ volume, animate: volume === 'streamlines' ? state.animate : false })} options={volumeOptions} />
        </div>}
      </div>
      {probe && probe.value !== null && <div className="pointer-events-none absolute z-10 rounded-lg border border-line bg-surface/95 px-2.5 py-1.5 text-xs shadow-sm" style={{ left: probe.screen.x + 14, top: probe.screen.y + 14 }}>
        <div className="font-semibold tabular-nums">{significant(probe.value, 4)} {unitOf(probe.layerId)}</div>
        {probe.position && <div className="text-muted tabular-nums">({probe.position.map((v) => significant(v, 3)).join(', ')}) m</div>}
        {probe.partIndex != null && manifest?.parts[probe.partIndex] && <div className="text-muted">{manifest.parts[probe.partIndex].name}</div>}
      </div>}
    </ViewerFrame>

    <button className="absolute right-3 top-3 z-20 grid h-8 w-8 place-items-center rounded-lg border border-line bg-surface/95 text-muted shadow-sm hover:text-ink"
      aria-label={panel ? 'Hide the options panel' : 'Show the options panel'} aria-expanded={panel} onClick={() => setPanel(!panel)}>
      {panel ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
    </button>
    {panel && <aside className="absolute bottom-3 right-3 top-14 z-10 w-[min(18.5rem,calc(100%-1.5rem))] space-y-2 overflow-y-auto pb-1 scrollbar-thin" aria-label="Flow visualisation options">
      <ResultsCard run={run} forces={forces} />
      {legacy && !isShare && <FloatCard title="Build the 3D results">
        <p className="hint">This run predates the results package, so only its pressure surface (if it was written) can be shown. Building the package adds pressure, friction and velocity maps, streamlines, loss clouds, vortices, slices, forces by part and insights. The solved case is not changed.</p>
        <button className="btn-primary btn-sm w-full" disabled={building} onClick={buildPackage}>{building ? 'Queued…' : 'Build results package'}</button>
      </FloatCard>}
      {!legacy && manifest && <>
        {animations.length > 0 && <FloatCard title="Transient animation">
          <Segmented size="sm" label="Animation" value={animation || 'off'} onChange={(v) => { setAnimation(v === 'off' ? null : v); setFrame(0); setPlaying(v !== 'off'); }}
            options={[{ value: 'off', label: 'Off' }, ...animations.map((l) => ({ value: l.id, label: l.id === 'animation_object' ? 'Surface' : 'Section' }))]} />
          {animated?.frames && <>
            <div className="flex items-center gap-2">
              <button className="btn-secondary btn-sm" aria-label={playing ? 'Pause' : 'Play'} onClick={() => setPlaying(!playing)} disabled={!frames}>{playing ? <Pause size={14} /> : <Play size={14} />}</button>
              <input type="range" className="w-full accent-[var(--accent)]" min={0} max={frameCount - 1} value={Math.min(frame, frameCount - 1)} aria-label="Animation frame"
                onChange={(e) => { setPlaying(false); setFrame(Number(e.target.value)); }} />
              <select className="input w-auto py-1 text-xs" aria-label="Playback speed" value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
                {[0.5, 1, 2].map((s) => <option key={s} value={s}>{s}×</option>)}
              </select>
            </div>
            <p className="hint tabular-nums">{frameData.isLoading ? 'Loading frames…' : frameData.error ? String((frameData.error as Error).message) :
              `t = ${significant(animated.frames.times_s[Math.min(frame, frameCount - 1)], 4)} s · frame ${Math.min(frame, frameCount - 1) + 1} of ${frameCount}`}</p>
          </>}
          <p className="hint">Instantaneous values over the averaging window. The other layers show time-averaged fields.</p>
        </FloatCard>}

        <FloatCard title={FIELD_NAMES[state.field] || fieldInfo.label || state.field} right={definition ? <InfoTip content={definition} /> : undefined}>
          {manifest.basis === 'time_averaged' && <p className="hint">Time-averaged over the transient averaging window.</p>}
          {state.field === 'U_near_mag' && <p className="hint">Velocity just off the surface ({significant(surface?.near_wall_offset_m, 2)} m out); the wall itself does not move.</p>}
          <div className="grid grid-cols-2 gap-2">
            <select className="input py-1.5 text-xs" aria-label="Colour map" value={state.colormap} onChange={(e) => set({ colormap: e.target.value as ColormapId })}>
              {Object.values(COLORMAPS).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
            <select className="input py-1.5 text-xs" aria-label="Colour range" value={state.rangeMode} onChange={(e) => set({ rangeMode: e.target.value as FlowState['rangeMode'], fixed: rangeFor(surfaceInfo, { ...state, rangeMode: 'default' }, state.field) || state.fixed })}>
              <option value="default">Recommended</option><option value="auto">Data range</option><option value="symmetric">Symmetric</option><option value="fixed">Fixed</option>
            </select>
          </div>
          {state.rangeMode === 'fixed' && <div className="grid grid-cols-2 gap-2">
            {[0, 1].map((i) => <input key={i} className="input py-1.5 text-xs" type="number" step="any" aria-label={i ? 'Maximum' : 'Minimum'} value={state.fixed[i]} onChange={(e) => { const v = e.target.valueAsNumber; if (Number.isFinite(v)) set({ fixed: (i ? [state.fixed[0], v] : [v, state.fixed[1]]) as [number, number] }); }} />)}
          </div>}
          {layers.friction_lines && <Toggle label="Friction lines" checked={state.friction} onChange={(friction) => set({ friction })} />}
          <Slider label="Model opacity" min={0.15} max={1} step={0.05} value={state.objectOpacity} onChange={(objectOpacity) => set({ objectOpacity })} display={`${Math.round(state.objectOpacity * 100)} %`} />
        </FloatCard>

        {state.volume !== 'none' && <FloatCard title={VOLUME_NAMES[state.volume]}>
          {state.volume === 'streamlines' && <>
            <Segmented size="sm" label="Seed planes" value={state.rakes} onChange={(rakesMode) => set({ rakes: rakesMode, plane: state.plane === null ? null : -1 })}
              options={[{ value: 'horizontal', label: 'Horizontal', disabled: !rakesOf(manifest, 'horizontal').length }, { value: 'vertical', label: 'Vertical', disabled: !rakesOf(manifest, 'vertical').length }]} />
            <Toggle label="All planes" checked={plane === null} onChange={(allPlanes) => set({ plane: allPlanes ? null : -1 })} />
            {plane !== null && rakes.length > 1 && <Slider label="Plane" min={0} max={rakes.length - 1} value={plane} onChange={(p) => set({ plane: p })} display={`${plane + 1} of ${rakes.length}`} />}
            <Slider label="Line density" min={1} max={20} value={state.lines} onChange={(lines) => set({ lines })} display={subsample === 1 ? 'every line' : `1 in ${subsample}`} />
            <Toggle label="Animate particles" checked={state.animate} onChange={(animate) => set({ animate })} />
            {state.animate && <Slider label="Particles per line" min={2} max={40} value={state.particles} onChange={(particles) => set({ particles })} display={state.particles} />}
            <p className="hint">Lines start upstream and follow the solved velocity. Particles replay the steady flow at its local speed; they are not a time-resolved simulation.</p>
          </>}
          {state.volume === 'cloud' && <>
            <Slider label="Cloud opacity" min={0.15} max={1} step={0.05} value={state.cloudOpacity} onChange={(cloudOpacity) => set({ cloudOpacity })} display={`${Math.round(state.cloudOpacity * 100)} %`} />
            <p className="hint">Red clouds enclose air that has lost all its total pressure (Cpt = 0). Larger, longer clouds usually mean more pressure drag.</p>
          </>}
          {state.volume === 'vortex' && <>
            <Segmented size="sm" label="Vortex level" value={vortex?.id || ''} onChange={(v) => set({ vortex: v })} options={q.map((l) => ({ value: l.id, label: `Q* ${l.iso?.normalized}` }))} />
            <p className="hint">Q-criterion iso-surfaces coloured by velocity; a higher level shows only the strongest vortex cores.</p>
          </>}
          {state.volume === 'noise' && <>
            <Segmented size="sm" label="Noise level" value={noiseLayer?.id || ''} onChange={(v) => set({ noise: v })} options={noise.map((l) => ({ value: l.id, label: `${l.iso?.value} dB` }))} />
            <p className="hint">Proudman estimate of where turbulence generates sound. A qualitative map, not an acoustic prediction.</p>
          </>}
          {state.volume === 'section' && stack && <>
            <Segmented size="sm" label="Slice axis" value={stack.axis as FlowState['section']} onChange={(section) => set({ section, sectionIndex: -1 })}
              options={(['x', 'y', 'z'] as const).map((axis) => ({ value: axis, label: axis.toUpperCase(), disabled: !stacks.some((s) => s.axis === axis) }))} />
            {stack.items && <Slider label="Position" min={0} max={stack.items.length - 1} value={sectionIndex} onChange={(i) => set({ sectionIndex: i })}
              display={`${String(stack.axis).toUpperCase()} = ${significant(sectionItem?.position_m, 4)} m`} />}
            <select className="input py-1.5 text-xs" aria-label="Slice field" value={state.sectionField} onChange={(e) => set({ sectionField: e.target.value })}>
              {stack.arrays.filter((a) => a.components === 1).map((a) => <option key={a.name} value={a.name}>{FIELD_NAMES[a.name] || a.label}</option>)}
            </select>
          </>}
          {state.volume === 'water' && <p className="hint">The air–water interface (water fraction 0.5), coloured by height.</p>}
        </FloatCard>}
      </>}
      <FloatCard title="Probe" right={<Switch checked={state.probe} onChange={(p) => set({ probe: p })} label="Hover probe" />}>
        <p className="hint">Hover the model{sectionItem ? ' or the slice' : ''} to read values (interpolated for display).</p>
      </FloatCard>
    </aside>}
  </div>;
}
