import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Pause, Play, Wind } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import type { ArrayInfo, Layer, Manifest, Run } from '../../api/types';
import { COLORMAPS, type ColormapId } from '../../lib/colormaps';
import { significant } from '../../lib/units';
import { EmptyState, Segmented, Switch, cx } from '../../ui';
import { prefetch } from '../../viewer/data';
import { Legend } from '../../viewer/Legend';
import { ViewerFrame } from '../../viewer/ViewerFrame';
import type { ProbeResult, ViewLayer, ViewportHandle } from '../../viewer/Viewport';
import { useViewerTheme } from '../../viewer/theme';
import { annotateScreenshot } from './screenshot';
import { isShare, shareFile } from '../../share/bundle';

export interface FlowState {
  field: string; colormap: ColormapId; rangeMode: 'auto' | 'symmetric' | 'fixed' | 'default'; fixed: [number, number];
  friction: boolean; cloud: boolean; cloudOpacity: number; vortex: string | null; noise: string | null;
  rakes: 'off' | 'horizontal' | 'vertical'; density: number; animate: boolean;
  section: 'off' | 'x' | 'y' | 'z'; sectionIndex: number; sectionField: string; objectOpacity: number; probe: boolean;
}

export const defaultFlowState = (manifest?: Manifest | null): FlowState => {
  const surface = manifest?.layers.find((l) => l.id === 'surface');
  const field = surface?.arrays.some((a) => a.name === 'Cp') ? 'Cp' : surface?.arrays.find((a) => !a.categorical)?.name || 'pressure_pa';
  return { field, colormap: (ARRAY_DEFAULT_MAP[field] || 'cool_to_warm'), rangeMode: 'default', fixed: [-1, 1], friction: false, cloud: false, cloudOpacity: 0.55,
    vortex: null, noise: null, rakes: 'off', density: 2, animate: false, section: 'off', sectionIndex: 0, sectionField: 'U_mag', objectOpacity: 1, probe: true };
};

const ARRAY_DEFAULT_MAP: Record<string, ColormapId> = { Cp: 'cool_to_warm', pressure_pa: 'cool_to_warm', Cf: 'rainbow', wall_shear_mag: 'rainbow', yPlus: 'viridis', U_mag: 'rainbow', Cpt: 'rainbow', sensitivity: 'advice', elevation_m: 'cool_to_warm', alpha_water: 'cool_to_warm' };
const FIELD_NAMES: Record<string, string> = { Cp: 'Pressure (Cp)', pressure_pa: 'Pressure', Cf: 'Friction (Cf)', wall_shear_mag: 'Wall shear', yPlus: 'y+', U_mag: 'Velocity', Cpt: 'Total pressure (Cpt)', sensitivity: 'Design advice', elevation_m: 'Elevation', alpha_water: 'Water fraction' };

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

function Group({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return <section className="space-y-2.5 border-b border-line px-4 py-3.5 last:border-b-0">
    <div className="flex items-center justify-between gap-2"><h3 className="eyebrow">{title}</h3>{right}</div>{children}
  </section>;
}

export function FlowTab({ run, manifest, legacy }: { run: Run; manifest: Manifest | null; legacy: boolean }) {
  const [state, setState] = useState<FlowState>(() => defaultFlowState(manifest));
  const [probe, setProbe] = useState<ProbeResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const viewer = useRef<ViewportHandle | null>(null);
  const theme = useViewerTheme();
  // Transient animation: which frame layer is shown, and playback. Kept outside FlowState so a
  // frame step swaps values in place instead of rebuilding the layer list.
  const [animation, setAnimation] = useState<string | null>(null);
  const [frame, setFrame] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(1);
  useEffect(() => { setState(defaultFlowState(manifest)); setAnimation(null); setFrame(0); setPlaying(false); }, [manifest?.run_id]);
  const set = (change: Partial<FlowState>) => setState((s) => ({ ...s, ...change }));

  const layers: Record<string, Layer> = Object.fromEntries((manifest?.layers || []).map((l) => [l.id, l]));
  const surface = layers.surface;
  // Point and cell copies of the same field appear once (point data is used for display).
  const surfaceArrays = (surface?.arrays || []).filter((a, i, all) => !a.categorical && a.components === 1 && all.findIndex((b) => b.name === a.name && b.components === 1) === i);
  const surfaceInfo = surfaceArrays.find((a) => a.name === state.field);
  const q = (manifest?.layers || []).filter((l) => l.id.startsWith('q_'));
  const noise = (manifest?.layers || []).filter((l) => l.id.startsWith('noise_'));
  const rakes = (manifest?.layers || []).filter((l) => l.kind === 'streamlines');
  const stacks = (manifest?.layers || []).filter((l) => l.kind === 'section_stack');
  const stack = stacks.find((s) => s.axis === state.section);
  const sectionItem = stack?.items?.[Math.min(state.sectionIndex, (stack.items?.length || 1) - 1)];
  const base = (path: string) => `/api/runs/${run.id}/files/${path}`;
  const animations = (manifest?.layers || []).filter((l) => l.kind === 'animation' && l.frames);
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
    const i = Math.min(state.sectionIndex, stack.items.length - 1);
    prefetch([stack.items[i - 1], stack.items[i + 1], stack.items[i + 2]].filter(Boolean).map((item) => base(item!.url)));
  }, [stack, state.sectionIndex]);

  const range = rangeFor(surfaceInfo, state, state.field) || [0, 1];
  const sectionInfo = stack?.arrays.find((a) => a.name === state.sectionField);
  const sectionRange: [number, number] = state.sectionField === 'Cp' ? [-1, 1] : (sectionInfo?.range as [number, number]) || [0, 1];
  const viewLayers: ViewLayer[] = useMemo(() => {
    if (legacy || !manifest) return [{ id: 'legacy', url: base('results/surface.vtp'), field: 'pressure_pa', colormap: state.colormap, range: null, pickable: true }];
    const out: ViewLayer[] = [];
    if (surface?.url) out.push({ id: 'surface', url: base(surface.url), field: state.field, colormap: state.colormap, range, opacity: state.objectOpacity, pickable: true });
    if (state.friction && layers.friction_lines?.url) out.push({ id: 'friction', url: base(layers.friction_lines.url), color: theme.dark ? [0.95, 0.97, 1] : [0.12, 0.16, 0.22], lineWidth: 1, pickable: false, ambient: 1 });
    if (state.cloud && layers.pressure_cloud?.url) out.push({ id: 'cloud', url: base(layers.pressure_cloud.url), color: [0.86, 0.15, 0.2], opacity: state.cloudOpacity, pickable: false });
    const vortex = q.find((l) => l.id === state.vortex);
    if (vortex?.url) out.push({ id: 'vortex', url: base(vortex.url), field: 'U_mag', colormap: 'rainbow', range: null, opacity: 0.85, pickable: false });
    const noiseLayer = noise.find((l) => l.id === state.noise);
    if (noiseLayer?.url) out.push({ id: 'noise', url: base(noiseLayer.url), color: [0.85, 0.11, 0.38], opacity: 0.35, pickable: false });
    if (state.rakes !== 'off') for (const rake of rakes.filter((r) => r.orientation === state.rakes)) out.push({ id: rake.id, url: base(rake.url!), field: 'U_mag', colormap: 'rainbow', range: null, subsample: state.density, lineWidth: 1.5, pickable: false });
    if (sectionItem) out.push({ id: 'section', url: base(sectionItem.url), field: state.sectionField, colormap: ARRAY_DEFAULT_MAP[state.sectionField] || 'rainbow', range: sectionRange, pickable: true, opacity: 0.95 });
    if (animated?.url && animated.frames) {
      // The animated object surface replaces the time-averaged one; a section adds to it.
      const onObject = animated.id === 'animation_object';
      const kept = onObject ? out.filter((l) => l.id !== 'surface') : out;
      kept.push({ id: 'animation', url: base(animated.url), field: animated.frames.array, colormap: onObject ? 'cool_to_warm' : 'rainbow',
        range: animationRange, pickable: true, opacity: onObject ? state.objectOpacity : 0.95 });
      return kept;
    }
    return out;
  }, [manifest, legacy, state, range[0], range[1], theme.dark, sectionItem?.url, animated?.id]);

  if (!manifest && !legacy) return <EmptyState icon={<Wind size={22} />} title="Results are being packaged">Surface fields, clouds, streamlines and sections appear when the results package completes.</EmptyState>;
  const fieldInfo = surfaceInfo || { name: state.field, unit: state.field === 'pressure_pa' ? 'Pa' : '1', label: FIELD_NAMES[state.field] } as ArrayInfo;
  const flow = manifest?.reference.frames.wind.drag;
  const lift = manifest?.reference.frames.wind.lift;
  const firstRake = rakes.find((r) => r.orientation === state.rakes);

  return <div className="grid overflow-hidden rounded-2xl border border-line bg-surface lg:h-[calc(100vh-15.5rem)] lg:min-h-[34rem] lg:grid-cols-[19rem_1fr]">
    <aside className="max-h-[50vh] overflow-y-auto border-b border-line scrollbar-thin lg:max-h-none lg:border-b-0 lg:border-r" aria-label="Flow visualisation controls">
      {!legacy && <>
        {animations.length > 0 && <Group title="Transient animation">
          <Segmented size="sm" label="Animation" value={animation || 'off'} onChange={(v) => { setAnimation(v === 'off' ? null : v); setFrame(0); setPlaying(v !== 'off'); }}
            options={[{ value: 'off', label: 'Off' }, ...animations.map((l) => ({ value: l.id, label: l.id === 'animation_object' ? 'Surface pressure' : 'Section velocity' }))]} />
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
          <p className="hint">Instantaneous values over the averaging window of the transient run. The other layers show time-averaged fields.</p>
        </Group>}
        <Group title="Surface">
          {manifest?.basis === 'time_averaged' && <p className="hint">Time-averaged over the transient averaging window.</p>}
          <div className="flex flex-wrap gap-1.5">{surfaceArrays.map((a) => <button key={a.name} className={cx('btn-secondary btn-sm', state.field === a.name && 'border-accent bg-accent-soft text-accent-ink')}
            onClick={() => set({ field: a.name, colormap: ARRAY_DEFAULT_MAP[a.name] || 'rainbow', rangeMode: 'default' })}>{FIELD_NAMES[a.name] || a.label}</button>)}</div>
          <div className="grid grid-cols-2 gap-2">
            <select className="input py-1.5 text-xs" aria-label="Colour map" value={state.colormap} onChange={(e) => set({ colormap: e.target.value as ColormapId })}>
              {Object.values(COLORMAPS).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
            </select>
            <select className="input py-1.5 text-xs" aria-label="Colour range" value={state.rangeMode} onChange={(e) => set({ rangeMode: e.target.value as FlowState['rangeMode'], fixed: rangeFor(surfaceInfo, { ...state, rangeMode: 'default' }, state.field) || state.fixed })}>
              <option value="default">Recommended range</option><option value="auto">Data range</option><option value="symmetric">Symmetric</option><option value="fixed">Fixed</option>
            </select>
          </div>
          {state.rangeMode === 'fixed' && <div className="grid grid-cols-2 gap-2">
            {[0, 1].map((i) => <input key={i} className="input py-1.5 text-xs" type="number" step="any" aria-label={i ? 'Maximum' : 'Minimum'} value={state.fixed[i]} onChange={(e) => { const v = e.target.valueAsNumber; if (Number.isFinite(v)) set({ fixed: (i ? [state.fixed[0], v] : [v, state.fixed[1]]) as [number, number] }); }} />)}
          </div>}
          {layers.friction_lines && <label className="flex items-center justify-between text-sm">Surface friction lines<Switch checked={state.friction} onChange={(friction) => set({ friction })} label="Surface friction lines" /></label>}
          <label className="block text-xs text-muted">Model opacity
            <input type="range" className="w-full accent-[var(--accent)]" min={0.15} max={1} step={0.05} value={state.objectOpacity} onChange={(e) => set({ objectOpacity: Number(e.target.value) })} /></label>
        </Group>
        <Group title="Drag" right={layers.pressure_cloud ? <Switch checked={state.cloud} onChange={(cloud) => set({ cloud })} label="Pressure loss clouds" /> : undefined}>
          {layers.pressure_cloud ? <>
            <p className="hint">Red clouds enclose air that has lost all its total pressure (Cpt = 0). Larger, longer clouds usually mean more pressure drag.</p>
            {state.cloud && <label className="block text-xs text-muted">Cloud opacity<input type="range" className="w-full accent-[var(--accent)]" min={0.15} max={1} step={0.05} value={state.cloudOpacity} onChange={(e) => set({ cloudOpacity: Number(e.target.value) })} /></label>}
          </> : <p className="hint">{manifest?.missing.find((m) => m.id === 'pressure_cloud')?.reason || 'Not available for this run.'}</p>}
        </Group>
        <Group title="Vortices">
          {q.length ? <Segmented size="sm" label="Vortex level" value={state.vortex || 'off'} onChange={(v) => set({ vortex: v === 'off' ? null : v })}
            options={[{ value: 'off', label: 'Off' }, ...q.map((l) => ({ value: l.id, label: `Q* ${l.iso?.normalized}` }))]} />
            : <p className="hint">{manifest?.missing.find((m) => m.id === 'q_criterion')?.reason || 'Not available for this run.'}</p>}
          <p className="hint">Q-criterion iso-surfaces coloured by velocity; a higher level shows only the strongest vortex cores.</p>
        </Group>
        <Group title="Noise (qualitative)">
          {noise.length ? <Segmented size="sm" label="Noise level" value={state.noise || 'off'} onChange={(v) => set({ noise: v === 'off' ? null : v })}
            options={[{ value: 'off', label: 'Off' }, ...noise.map((l) => ({ value: l.id, label: `${l.iso?.value} dB` }))]} />
            : <p className="hint">{manifest?.missing.find((m) => m.id === 'noise_cloud')?.reason || 'Not available for this run.'}</p>}
          <p className="hint">Proudman estimate of where turbulence generates sound. A qualitative map, not an acoustic prediction.</p>
        </Group>
        <Group title="Streamlines">
          <Segmented size="sm" label="Streamline rakes" value={state.rakes} onChange={(rakesMode) => set({ rakes: rakesMode, animate: rakesMode === 'off' ? false : state.animate })}
            options={[{ value: 'off', label: 'Off' }, { value: 'horizontal', label: 'Horizontal', disabled: !rakes.some((r) => r.orientation === 'horizontal') }, { value: 'vertical', label: 'Vertical', disabled: !rakes.some((r) => r.orientation === 'vertical') }]} />
          {state.rakes !== 'off' && <>
            <label className="block text-xs text-muted">Density<input type="range" className="w-full accent-[var(--accent)]" min={1} max={6} step={1} value={7 - state.density} onChange={(e) => set({ density: 7 - Number(e.target.value) })} /></label>
            <label className="flex items-center justify-between text-sm">Animate particles<Switch checked={state.animate} onChange={(animate) => set({ animate })} label="Animate particles" /></label>
            <p className="hint">Particles move along steady streamlines; this is playback, not a time-resolved simulation.</p>
          </>}
        </Group>
        <Group title="Sections">
          <Segmented size="sm" label="Section axis" value={state.section} onChange={(section) => set({ section, sectionIndex: Math.floor(((stacks.find((s) => s.axis === section)?.items?.length) || 1) / 2) })}
            options={[{ value: 'off', label: 'Off' }, ...(['x', 'y', 'z'] as const).map((axis) => ({ value: axis, label: axis.toUpperCase(), disabled: !stacks.some((s) => s.axis === axis) }))]} />
          {stack?.items && <>
            <input type="range" className="w-full accent-[var(--accent)]" min={0} max={stack.items.length - 1} value={Math.min(state.sectionIndex, stack.items.length - 1)} onChange={(e) => set({ sectionIndex: Number(e.target.value) })} aria-label="Section position" />
            <p className="hint">{state.section.toUpperCase()} = {significant(sectionItem?.position_m, 4)} m · {Math.min(state.sectionIndex, stack.items.length - 1) + 1} of {stack.items.length}</p>
            <select className="input py-1.5 text-xs" aria-label="Section field" value={state.sectionField} onChange={(e) => set({ sectionField: e.target.value })}>
              {stack.arrays.filter((a) => a.components === 1).map((a) => <option key={a.name} value={a.name}>{FIELD_NAMES[a.name] || a.label}</option>)}
            </select>
          </>}
        </Group>
      </>}
      {legacy && <Group title="Surface"><p className="hint">This run predates the results package: only the pressure surface is available. Re-run the results package from Downloads for clouds, streamlines and sections.</p></Group>}
      <Group title="Probe" right={<Switch checked={state.probe} onChange={(p) => set({ probe: p })} label="Hover probe" />}><p className="hint">Hover the model or a section to read values (interpolated for display).</p></Group>
    </aside>
    <ViewerFrame ref={viewer} className="h-[60vh] lg:h-auto" layers={viewLayers} bounds={manifest?.bounds.model} presets={manifest?.camera_presets} flow={flow} lift={lift}
      probeLayer={!state.probe ? null : animated?.frames ? { layerId: 'animation', field: animated.frames.array }
        : { layerId: sectionItem ? 'section' : 'surface', field: sectionItem ? state.sectionField : state.field }}
      frames={frames} onProbe={setProbe} tracerLayer={state.animate && firstRake ? firstRake.id : null} onLoading={setLoading} onError={setError} loading={loading} error={error}
      screenshotName={`${run.spec.name || 'gustsim'}-${state.field}.png`}
      onScreenshot={(image) => annotateScreenshot(image, { title: run.spec.name, subtitle: `${FIELD_NAMES[state.field] || state.field}`, colormap: state.colormap, range, unit: fieldInfo.unit || undefined, status: manifest?.status })}>
      <div className="pointer-events-none absolute bottom-3 right-3 flex flex-col items-end gap-2">
        {state.section !== 'off' && sectionItem && <Legend colormap={ARRAY_DEFAULT_MAP[state.sectionField] || 'rainbow'} range={sectionRange} label={`Section · ${FIELD_NAMES[state.sectionField] || state.sectionField}`} unit={sectionInfo?.unit} />}
        {animated?.frames && <Legend colormap={animated.id === 'animation_object' ? 'cool_to_warm' : 'rainbow'} range={animationRange} label={`Instantaneous · ${FIELD_NAMES[animated.frames.array] || animated.frames.array}`} unit={animated.frames.unit || (animated.frames.array === 'Cp' ? '1' : undefined)} />}
        {!(animated?.id === 'animation_object') && <Legend colormap={state.colormap} range={range} label={FIELD_NAMES[state.field] || fieldInfo.label || state.field} unit={fieldInfo.unit} />}
      </div>
      {probe && probe.value !== null && <div className="pointer-events-none absolute rounded-lg border border-line bg-surface/95 px-2.5 py-1.5 text-xs shadow-sm" style={{ left: probe.screen.x + 14, top: probe.screen.y + 14 }}>
        <div className="font-semibold tabular-nums">{significant(probe.value, 4)} {probe.layerId === 'section' ? (sectionInfo?.unit !== '1' ? sectionInfo?.unit : '') : (fieldInfo.unit !== '1' ? fieldInfo.unit : '')}</div>
        {probe.position && <div className="text-muted tabular-nums">({probe.position.map((v) => significant(v, 3)).join(', ')}) m</div>}
        {probe.partIndex != null && manifest?.parts[probe.partIndex] && <div className="text-muted">{manifest.parts[probe.partIndex].name}</div>}
      </div>}
    </ViewerFrame>
  </div>;
}
