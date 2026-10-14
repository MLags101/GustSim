import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import '@kitware/vtk.js/Rendering/OpenGL/Profiles/Geometry';
import vtkGenericRenderWindow from '@kitware/vtk.js/Rendering/Misc/GenericRenderWindow';
import vtkPolyData from '@kitware/vtk.js/Common/DataModel/PolyData';
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray';
import vtkMapper from '@kitware/vtk.js/Rendering/Core/Mapper';
import vtkActor from '@kitware/vtk.js/Rendering/Core/Actor';
import vtkColorTransferFunction from '@kitware/vtk.js/Rendering/Core/ColorTransferFunction';
import vtkInteractorStyleManipulator from '@kitware/vtk.js/Interaction/Style/InteractorStyleManipulator';
import vtkMouseCameraTrackballRotateManipulator from '@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballRotateManipulator';
import vtkMouseCameraTrackballPanManipulator from '@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballPanManipulator';
import vtkMouseCameraTrackballZoomManipulator from '@kitware/vtk.js/Interaction/Manipulators/MouseCameraTrackballZoomManipulator';
import vtkOrientationMarkerWidget from '@kitware/vtk.js/Interaction/Widgets/OrientationMarkerWidget';
import vtkAnnotatedCubeActor from '@kitware/vtk.js/Rendering/Core/AnnotatedCubeActor';
import vtkOpenGLHardwareSelector from '@kitware/vtk.js/Rendering/OpenGL/HardwareSelector';
import { FieldAssociations } from '@kitware/vtk.js/Common/DataModel/DataSet/Constants';
import type { CameraPreset, SceneData, Vec3 } from '../api/types';
import { COLORMAPS, type ColormapId } from '../lib/colormaps';
import { loadPolyData, arrayInfo } from './data';
import { overlayGeometry, groundPlane, dimensionLines, orientedBoxes } from './overlays';
import { tracksFromPolyData, playbackPlan, particlePositions } from './playback';

export interface ViewLayer {
  id: string;
  url: string;
  field?: string | null;
  colormap?: ColormapId;
  range?: [number, number] | null;
  color?: [number, number, number];
  opacity?: number;
  visible?: boolean;
  lineWidth?: number;
  pickable?: boolean;
  subsample?: number;
  edges?: boolean;
  ambient?: number;
  /** Hide this other layer once this one has loaded (an animated copy of the same surface). */
  replaces?: string;
}
export interface GeometryView {
  scene: SceneData;
  selectedGroups?: number[];
  hiddenGroups?: number[];
  highlightGroups?: number[];
  onPick?: (group: number, modifiers: { ctrl: boolean; shift: boolean }) => void;
  opacity?: number;
  colors?: { neutral: number[]; selected: number[]; highlight: number[] };
  /** Column-major 4×4 preview transform (not applied to the stored geometry). */
  matrix?: number[] | null;
}
export interface Overlays {
  flow?: any; domain?: any; rotation?: any; bounds?: number[][];
  ground?: { up: number[]; offset: number } | null;
  dimensions?: boolean;
  boxes?: { center: number[]; axis: number[]; up: number[]; size: number[] }[];
  color?: [number, number, number];
}
export interface ProbeResult { layerId: string; field: string | null; value: number | null; components?: number[]; position: Vec3 | null; screen: { x: number; y: number }; cellId: number; partIndex?: number | null }
export interface AnchorPosition { x: number; y: number; visible: boolean }
export interface ViewportHandle { capture: () => Promise<string>; fit: () => void; camera: () => CameraPreset | null }

interface Props {
  geometry?: GeometryView | null;
  layers?: ViewLayer[];
  overlays?: Overlays | null;
  cameraRequest?: { camera?: CameraPreset | null; fit?: boolean; nonce: number } | null;
  onCameraChange?: (camera: CameraPreset) => void;
  syncCamera?: CameraPreset | null;
  probeLayer?: { layerId: string; field: string | null } | null;
  onProbe?: (result: ProbeResult | null) => void;
  onPickLayer?: (result: ProbeResult, modifiers: { ctrl: boolean; shift: boolean }) => void;
  anchors?: { id: string; point: Vec3 }[];
  onAnchors?: (positions: Record<string, AnchorPosition>) => void;
  onDimensionLabels?: (labels: { axis: number; length: number; x: number; y: number; visible: boolean }[]) => void;
  background: [number, number, number];
  /** Per-layer replacement values for one field (transient animation frames), applied in place. */
  frames?: Record<string, { field: string; values: Float32Array }> | null;
  /** Camera direction for the first framing (e.g. the manifest's flow-relative 3D preset). */
  initialCamera?: CameraPreset | null;
  /** Streamline layers whose particles are animated, and particles per typical streamline. */
  tracerLayers?: string[] | null;
  tracerDensity?: number;
  /** Particle size in CSS pixels and colour (RGB 0–1); chosen to contrast with the streamlines. */
  tracerSize?: number;
  tracerColor?: [number, number, number];
  label?: string;
  onLoading?: (loading: boolean) => void;
  onError?: (message: string | null) => void;
}

const nearly = (a: number[], b: number[]) => a.every((v, i) => Math.abs(v - b[i]) < 1e-9 * Math.max(1, Math.abs(v)));

function lookupTable(colormap: ColormapId, range: [number, number], vector: boolean) {
  const lut = vtkColorTransferFunction.newInstance();
  const map = COLORMAPS[colormap] ?? COLORMAPS.cool_to_warm;
  const [low, high] = range[0] === range[1] ? [range[0], range[0] + 1] : range;
  for (const [t, r, g, b] of map.points) lut.addRGBPoint(low + t * (high - low), r, g, b);
  lut.setMappingRange(low, high);
  lut.updateRange();
  if (vector) lut.setVectorModeToMagnitude();
  (lut as any).setNanColor?.(0.6, 0.6, 0.6, 1);
  return lut;
}

/** Keep only streamlines whose seed index is a multiple of `every` (rake density slider). */
function subsampleLines(poly: any, every: number) {
  if (every <= 1) return poly;
  const seeds = poly.getCellData().getArrayByName('SeedIds')?.getData();
  if (!seeds) return poly;
  const lines = poly.getLines().getData();
  const kept: number[] = [];
  for (let i = 0, cell = 0; i < lines.length; cell++) {
    const count = lines[i];
    if (Math.round(seeds[cell]) % every === 0) for (let k = 0; k <= count; k++) kept.push(lines[i + k]);
    i += count + 1;
  }
  const out = vtkPolyData.newInstance();
  out.setPoints(poly.getPoints());
  const pointData = poly.getPointData();
  for (let i = 0; i < pointData.getNumberOfArrays(); i++) out.getPointData().addArray(pointData.getArrayByIndex(i));
  out.getLines().setData(new Uint32Array(kept));
  return out;
}

function presetCamera(renderer: any, camera: CameraPreset) {
  const c = renderer.getActiveCamera();
  c.setFocalPoint(...camera.focal_point);
  c.setPosition(...camera.position);
  c.setViewUp(...camera.view_up);
  c.orthogonalizeViewUp();
}

export const Viewport = forwardRef<ViewportHandle, Props>(function Viewport(props, ref) {
  const container = useRef<HTMLDivElement>(null);
  const scene = useRef<any>(null);
  const latest = useRef(props);
  latest.current = props;
  const [ready, setReady] = useState(0);
  const [loaded, setLoaded] = useState(0);

  useImperativeHandle(ref, () => ({
    capture: async () => {
      const handle = scene.current;
      if (!handle) throw new Error('The viewer is not ready');
      handle.renderWindow.render();
      const [image] = handle.renderWindow.captureImages();
      return await image;
    },
    fit: () => {
      const handle = scene.current;
      if (!handle) return;
      handle.renderer.resetCamera();
      handle.renderer.resetCameraClippingRange();
      handle.renderWindow.render();
    },
    camera: () => {
      const c = scene.current?.renderer.getActiveCamera();
      return c ? { position: c.getPosition(), focal_point: c.getFocalPoint(), view_up: c.getViewUp() } : null;
    },
  }));

  // --- mount once ----------------------------------------------------------------------
  useEffect(() => {
    if (!container.current) return;
    const render = vtkGenericRenderWindow.newInstance({ background: [...latest.current.background, 1] as any, listenWindowResize: false });
    render.setContainer(container.current);
    const renderer = render.getRenderer();
    const renderWindow = render.getRenderWindow();
    const apiWindow: any = render.getApiSpecificRenderWindow();
    const interactor = render.getInteractor();
    const manipulators = [
      vtkMouseCameraTrackballRotateManipulator.newInstance({ button: 1 }),
      vtkMouseCameraTrackballPanManipulator.newInstance({ button: 3 }),
      vtkMouseCameraTrackballPanManipulator.newInstance({ button: 2 }),
      vtkMouseCameraTrackballPanManipulator.newInstance({ button: 1, shift: true }),
      vtkMouseCameraTrackballZoomManipulator.newInstance({ scrollEnabled: true }),
      vtkMouseCameraTrackballZoomManipulator.newInstance({ button: 3, control: true }),
    ];
    const style = vtkInteractorStyleManipulator.newInstance();
    manipulators.forEach((m) => style.addMouseManipulator(m));
    interactor.setInteractorStyle(style);
    const canvas = container.current.querySelector('canvas');
    const noMenu = (e: Event) => e.preventDefault();
    canvas?.addEventListener('contextmenu', noMenu);

    const cube = vtkAnnotatedCubeActor.newInstance();
    cube.setDefaultStyle({ text: '', fontFamily: 'Figtree Variable, Arial, sans-serif', fontColor: '#ffffff', fontStyle: 'bold',
      fontSizeScale: (res: number) => res / 2.1, edgeThickness: 0.06, edgeColor: '#0f172a', resolution: 256 } as any);
    cube.setXPlusFaceProperty({ text: '+X', faceColor: '#ef4444' } as any);
    cube.setXMinusFaceProperty({ text: '-X', faceColor: '#991b1b' } as any);
    cube.setYPlusFaceProperty({ text: '+Y', faceColor: '#22c55e' } as any);
    cube.setYMinusFaceProperty({ text: '-Y', faceColor: '#166534' } as any);
    cube.setZPlusFaceProperty({ text: '+Z', faceColor: '#3b82f6' } as any);
    cube.setZMinusFaceProperty({ text: '-Z', faceColor: '#1e3a8a' } as any);
    const orientation = vtkOrientationMarkerWidget.newInstance({ actor: cube, interactor });
    orientation.setParentRenderer(renderer);
    orientation.setEnabled(true);
    orientation.setViewportCorner(vtkOrientationMarkerWidget.Corners.BOTTOM_LEFT);
    orientation.setViewportSize(0.12);
    orientation.setMinPixelSize(64);
    orientation.setMaxPixelSize(96);

    const selector = vtkOpenGLHardwareSelector.newInstance({ captureZValues: true } as any);
    selector.setFieldAssociation(FieldAssociations.FIELD_ASSOCIATION_CELLS);
    selector.attach(apiWindow, renderer);

    const handle: any = {
      render, renderer, renderWindow, apiWindow, interactor, selector,
      layers: new Map<string, any>(), geometry: null, overlays: [] as any[], tracer: null,
      applyingSync: false, frame: 0, propsRef: latest,
    };
    scene.current = handle;

    const emitCamera = () => {
      if (handle.frame) return;
      handle.frame = requestAnimationFrame(() => {
        handle.frame = 0;
        const p = latest.current;
        const camera = renderer.getActiveCamera();
        const state = { position: camera.getPosition(), focal_point: camera.getFocalPoint(), view_up: camera.getViewUp() } as CameraPreset;
        if (!handle.applyingSync) p.onCameraChange?.(state);
        updateAnchors(handle, p);
      });
    };
    const cameraSubscription = renderer.getActiveCamera().onModified(emitCamera);
    handle.emitCamera = emitCamera;

    // Click (no drag) picks; hover probes. Both use the GPU selector.
    let down: { x: number; y: number } | null = null;
    const pressSub = interactor.onLeftButtonPress((event: any) => { down = event.position ? { x: event.position.x, y: event.position.y } : null; });
    const releaseSub = interactor.onLeftButtonRelease((event: any) => {
      if (!down || !event.position) return;
      const moved = Math.hypot(event.position.x - down.x, event.position.y - down.y);
      down = null;
      if (moved > 5) return;
      pick(handle, event.position.x, event.position.y).then((hit) => {
        if (!hit) return;
        const modifiers = { ctrl: !!(event.controlKey || event.metaKey), shift: !!event.shiftKey };
        const p = latest.current;
        if (hit.geometryGroup !== undefined) p.geometry?.onPick?.(hit.geometryGroup, modifiers);
        else if (hit.result) p.onPickLayer?.(hit.result, modifiers);
      }).catch(() => undefined);
    });
    let lastProbe = 0;
    let probeTimer: ReturnType<typeof setTimeout> | null = null;
    // vtk reports the first move after a pause as StartMouseMove, so both events probe.
    const probe = (event: any) => {
      const p = latest.current;
      if (!p.probeLayer || !p.onProbe || !event.position) return;
      const now = performance.now();
      const run = () => {
        lastProbe = performance.now();
        pick(handle, event.position.x, event.position.y).then((hit) => latest.current.onProbe?.(hit?.result ?? null)).catch((e) => console.warn('Probe failed', e));
      };
      if (probeTimer) clearTimeout(probeTimer);
      if (now - lastProbe > 70) run(); else probeTimer = setTimeout(run, 70);
    };
    const moveSub = interactor.onMouseMove(probe);
    const startMoveSub = interactor.onStartMouseMove(probe);
    const leave = () => latest.current.onProbe?.(null);
    canvas?.addEventListener('mouseleave', leave);

    const observer = new ResizeObserver(() => { render.resize(); updateAnchors(handle, latest.current); });
    observer.observe(container.current);
    setReady((v) => v + 1);

    return () => {
      scene.current = null;
      cancelAnimationFrame(handle.frame);
      if (probeTimer) clearTimeout(probeTimer);
      observer.disconnect();
      canvas?.removeEventListener('contextmenu', noMenu);
      canvas?.removeEventListener('mouseleave', leave);
      cameraSubscription.unsubscribe(); pressSub.unsubscribe(); releaseSub.unsubscribe(); moveSub.unsubscribe(); startMoveSub.unsubscribe();
      orientation.setEnabled(false); orientation.delete(); cube.delete();
      manipulators.forEach((m) => m.delete()); style.delete(); selector.delete();
      for (const entry of handle.layers.values()) { renderer.removeActor(entry.actor); entry.actor.delete(); entry.mapper.delete(); entry.lut?.delete(); }
      if (handle.geometry) { handle.geometry.actor.delete(); handle.geometry.mapper.delete(); handle.geometry.poly.delete(); }
      handle.overlays.forEach((o: any) => o.delete?.());
      // Release the WebGL context now; browsers cap live contexts and blank the oldest canvas.
      const gl = apiWindow.getContext?.();
      render.delete();
      gl?.getExtension?.('WEBGL_lose_context')?.loseContext();
    };
  }, []);

  // --- background --------------------------------------------------------------------------
  useEffect(() => {
    const handle = scene.current;
    if (!handle) return;
    handle.renderer.setBackground(...props.background, 1);
    handle.renderWindow.render();
  }, [ready, props.background[0], props.background[1], props.background[2]]);

  // --- component geometry (setup) ----------------------------------------------------------
  const sceneData = props.geometry?.scene;
  const hiddenKey = (props.geometry?.hiddenGroups || []).join(',');
  useEffect(() => {
    const handle = scene.current;
    if (!handle) return;
    if (handle.geometry) {
      handle.renderer.removeActor(handle.geometry.actor);
      handle.geometry.actor.delete(); handle.geometry.mapper.delete(); handle.geometry.poly.delete();
      handle.geometry = null;
    }
    if (!sceneData) { handle.renderWindow.render(); return; }
    const hidden = new Set(props.geometry?.hiddenGroups || []);
    const polys: number[] = [], groups: number[] = [];
    for (let i = 0, cell = 0; i < sceneData.polys.length;) {
      const count = sceneData.polys[i], group = sceneData.groups[cell++];
      if (!hidden.has(group)) { for (let k = 0; k <= count; k++) polys.push(sceneData.polys[i + k]); groups.push(group); }
      i += count + 1;
    }
    const poly = vtkPolyData.newInstance();
    poly.getPoints().setData(new Float32Array(sceneData.points), 3);
    poly.getPolys().setData(new Uint32Array(polys));
    const colors = vtkDataArray.newInstance({ name: 'componentColors', numberOfComponents: 3, values: new Uint8Array(groups.length * 3) });
    poly.getCellData().setScalars(colors);
    const mapper = vtkMapper.newInstance();
    mapper.setInputData(poly);
    mapper.setScalarModeToUseCellData();
    mapper.setColorModeToDirectScalars();
    mapper.setScalarVisibility(true);
    const actor = vtkActor.newInstance();
    actor.setMapper(mapper);
    const property = actor.getProperty();
    property.setSpecular(0.25); property.setSpecularPower(30); property.setDiffuse(0.9); property.setAmbient(0.12);
    handle.renderer.addActor(actor);
    handle.geometry = { actor, mapper, poly, colors, groups };
    recolor(handle, latest.current.geometry);
    fitIfFirst(handle);
    handle.renderWindow.render();
  }, [ready, sceneData, hiddenKey]);

  const selectionKey = JSON.stringify([props.geometry?.selectedGroups, props.geometry?.highlightGroups, props.geometry?.colors, props.geometry?.opacity, props.geometry?.matrix]);
  useEffect(() => {
    const handle = scene.current;
    if (!handle?.geometry) return;
    recolor(handle, latest.current.geometry);
    handle.geometry.actor.getProperty().setOpacity(latest.current.geometry?.opacity ?? 1);
    const matrix = latest.current.geometry?.matrix;
    handle.geometry.actor.setUserMatrix(Float64Array.from(matrix || [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]));
    handle.renderWindow.render();
  }, [ready, selectionKey, sceneData, hiddenKey]);

  // --- result layers -------------------------------------------------------------------------
  const layersKey = JSON.stringify(props.layers || []);
  useEffect(() => {
    const handle = scene.current;
    if (!handle) return;
    let alive = true;
    latest.current.onError?.(null);
    const wanted = new Map((props.layers || []).map((l) => [l.id, l]));
    for (const [id, entry] of handle.layers) {
      if (!wanted.has(id)) {
        handle.renderer.removeActor(entry.actor);
        entry.actor.delete(); entry.mapper.delete(); entry.lut?.delete();
        entry.restore?.();
        handle.layers.delete(id);
      }
    }
    const loads: Promise<void>[] = [];
    for (const layer of wanted.values()) {
      let entry = handle.layers.get(layer.id);
      if (!entry) {
        const mapper = vtkMapper.newInstance();
        const actor = vtkActor.newInstance();
        actor.setMapper(mapper);
        actor.setVisibility(false);
        handle.renderer.addActor(actor);
        entry = { actor, mapper, url: null, source: null, poly: null, lut: null, subsample: 1 };
        handle.layers.set(layer.id, entry);
      }
      const current = entry;
      if (current.url !== layer.url) {
        current.url = layer.url;
        // The load belongs to the layer entry, not to this effect run, so a re-run (React
        // StrictMode, a quick prop change) waits for it instead of dropping its result.
        current.loading = loadPolyData(layer.url).then((poly) => {
          if (current.url !== layer.url) return;
          current.source = poly;
          current.subsample = -1;
        }).catch((error) => { if (current.url === layer.url) latest.current.onError?.(String(error.message || error)); })
          .finally(() => { if (current.url === layer.url) current.loading = null; });
      }
      if (current.loading) loads.push(current.loading);
    }
    latest.current.onLoading?.(loads.length > 0);
    Promise.all(loads).then(() => {
      if (!alive || scene.current !== handle) return;
      latest.current.onLoading?.(false);
      const first = !handle.fitted;
      for (const layer of wanted.values()) applyLayer(handle, layer);
      for (const layer of wanted.values()) {
        const replaced = layer.replaces ? handle.layers.get(layer.replaces) : null;
        if (replaced && handle.layers.get(layer.id)?.source) replaced.actor.setVisibility(false);
      }
      if (first && [...handle.layers.values()].some((e: any) => e.source)) fitIfFirst(handle);
      // Layers loaded after the first framing (streamlines, clouds) must not be clipped.
      handle.renderer.resetCameraClippingRange();
      handle.renderWindow.render();
      updateAnchors(handle, latest.current);
      setLoaded((v) => v + 1);
    });
    return () => { alive = false; };
  }, [ready, layersKey]);

  // --- animation frames: swap one field's values without reloading geometry ------------------
  useEffect(() => {
    const handle = scene.current;
    if (!handle || !props.frames) return;
    let changed = false;
    for (const [id, frame] of Object.entries(props.frames)) {
      const entry = handle.layers.get(id);
      const info = entry?.poly ? arrayInfo(entry.poly, frame.field) : null;
      if (!info || info.array.getNumberOfValues() !== frame.values.length) continue;
      if (!entry.restore) {
        const original = info.array.getData(), array = info.array, poly = entry.poly;
        entry.restore = () => { array.setData(original, 1); poly.modified(); };
      }
      info.array.setData(frame.values, 1);
      entry.poly.modified();
      changed = true;
    }
    if (changed) handle.renderWindow.render();
  }, [ready, loaded, props.frames]);

  // --- overlays -----------------------------------------------------------------------------
  const overlayKey = JSON.stringify(props.overlays || null);
  useEffect(() => {
    const handle = scene.current;
    if (!handle) return;
    const o = props.overlays;
    const made: any[] = [];
    const color = o?.color || [0.96, 0.62, 0.15];
    if (o && (o.flow || o.domain || o.rotation?.enabled)) {
      const lines = overlayGeometry(o.rotation, o.domain, o.flow, o.bounds);
      made.push(addActor(handle, lines, (actor) => { actor.getProperty().setColor(...color); actor.getProperty().setLineWidth(2); }));
    }
    if (o?.ground && o.bounds) {
      const plane = groundPlane(o.bounds, o.ground.up, o.ground.offset);
      made.push(addActor(handle, plane, (actor) => { actor.getProperty().setColor(0.62, 0.66, 0.72); actor.getProperty().setOpacity(0.35); actor.getProperty().setAmbient(0.6); }));
    }
    if (o?.boxes?.length) {
      made.push(addActor(handle, orientedBoxes(o.boxes), (actor) => { actor.getProperty().setColor(0.25, 0.33, 0.77); actor.getProperty().setLineWidth(2); }));
    }
    let labels: any[] = [];
    if (o?.dimensions && o.bounds) {
      const dims = dimensionLines(o.bounds);
      labels = dims.labels;
      made.push(addActor(handle, dims.poly, (actor) => { actor.getProperty().setColor(0.94, 0.27, 0.27); actor.getProperty().setLineWidth(1.5); }));
    }
    handle.dimensionLabels = labels;
    // Frame the model together with its flow arrows the first time they appear.
    if (made.length && !handle.overlayFitted && (handle.geometry || handle.layers.size)) {
      handle.overlayFitted = true;
      handle.renderer.resetCamera();
      handle.renderer.resetCameraClippingRange();
    }
    handle.renderWindow.render();
    updateAnchors(handle, latest.current);
    return () => {
      for (const item of made) { handle.renderer.removeActor(item.actor); item.actor.delete(); item.mapper.delete(); item.poly.delete?.(); }
      handle.dimensionLabels = [];
      if (scene.current === handle) handle.renderWindow.render();
    };
  }, [ready, overlayKey]);

  // --- camera requests and sync --------------------------------------------------------------
  const request = props.cameraRequest;
  useEffect(() => {
    const handle = scene.current;
    if (!handle || !request) return;
    if (request.camera) presetCamera(handle.renderer, request.camera);
    if (request.fit || !request.camera) handle.renderer.resetCamera();
    else {
      // Keep the requested direction but frame what is visible.
      const camera = handle.renderer.getActiveCamera();
      const direction = camera.getDirectionOfProjection();
      handle.renderer.resetCamera();
      const focal = camera.getFocalPoint();
      const distance = camera.getDistance();
      camera.setPosition(...focal.map((f: number, i: number) => f - direction[i] * distance) as Vec3);
      camera.setViewUp(...request.camera.view_up);
      camera.orthogonalizeViewUp();
    }
    handle.renderer.resetCameraClippingRange();
    handle.renderWindow.render();
  }, [ready, request?.nonce]);

  useEffect(() => {
    const handle = scene.current;
    const sync = props.syncCamera;
    if (!handle || !sync) return;
    const camera = handle.renderer.getActiveCamera();
    if (nearly(camera.getPosition(), sync.position) && nearly(camera.getFocalPoint(), sync.focal_point)) return;
    handle.applyingSync = true;
    presetCamera(handle.renderer, sync);
    handle.renderer.resetCameraClippingRange();
    handle.renderWindow.render();
    requestAnimationFrame(() => { handle.applyingSync = false; });
  }, [ready, props.syncCamera]);

  // --- anchors (insight pins, labels) ---------------------------------------------------------
  const anchorsKey = JSON.stringify(props.anchors || []);
  useEffect(() => { const handle = scene.current; if (handle) updateAnchors(handle, latest.current); }, [ready, anchorsKey]);

  // --- steady streamline tracer playback ------------------------------------------------------
  const tracerKey = JSON.stringify([props.tracerLayers || [], props.tracerDensity ?? 10, props.tracerSize ?? 7, props.tracerColor || null]);
  useEffect(() => {
    const handle = scene.current;
    const layerIds = props.tracerLayers || [];
    if (!handle || !layerIds.length) return;
    // Wait until the streamline layers have loaded (and been subsampled) before building tracks.
    const entries = layerIds.map((id) => handle.layers.get(id));
    if (entries.some((entry) => !entry?.poly)) return;
    const tracks = entries.flatMap((entry) => tracksFromPolyData(entry.poly));
    if (!tracks.length) return;
    const plan = playbackPlan(tracks, props.tracerDensity ?? 10);
    const particles = vtkPolyData.newInstance();
    particles.getPoints().setData(new Float32Array(plan.total * 3), 3);
    const verts = new Uint32Array(plan.total * 2);
    for (let i = 0; i < plan.total; i++) { verts[2 * i] = 1; verts[2 * i + 1] = i; }
    particles.getVerts().setData(verts);
    const mapper = vtkMapper.newInstance(); mapper.setInputData(particles); mapper.setScalarVisibility(false);
    const actor = vtkActor.newInstance(); actor.setMapper(mapper);
    actor.getProperty().setColor(...(props.tracerColor || [0.07, 0.09, 0.15])); actor.getProperty().setAmbient(1); actor.getProperty().setDiffuse(0);
    // Point size is in device pixels; scale so the CSS size is the same on any display.
    actor.getProperty().setPointSize((props.tracerSize ?? 7) * (window.devicePixelRatio || 1));
    actor.setPickable(false);
    handle.renderer.addActor(actor);
    const began = performance.now();
    let frameId = 0;
    const frame = (now: number) => {
      if (scene.current !== handle) return;
      particlePositions(tracks, plan, (now - began) / 1000, particles.getPoints().getData() as Float32Array);
      particles.getPoints().modified(); particles.modified();
      handle.renderWindow.render();
      frameId = requestAnimationFrame(frame);
    };
    frameId = requestAnimationFrame(frame);
    return () => {
      cancelAnimationFrame(frameId);
      if (scene.current === handle) { handle.renderer.removeActor(actor); handle.renderWindow.render(); }
      actor.delete(); mapper.delete(); particles.delete();
    };
  }, [ready, loaded, tracerKey]);

  function onKeyDown(event: React.KeyboardEvent) {
    const handle = scene.current;
    if (!handle) return;
    const camera = handle.renderer.getActiveCamera();
    const step = event.shiftKey ? 15 : 5;
    if (event.key === 'ArrowLeft') camera.azimuth(step);
    else if (event.key === 'ArrowRight') camera.azimuth(-step);
    else if (event.key === 'ArrowUp') camera.elevation(step);
    else if (event.key === 'ArrowDown') camera.elevation(-step);
    else if (event.key === '+' || event.key === '=') camera.dolly(1.1);
    else if (event.key === '-' || event.key === '_') camera.dolly(0.9);
    else return;
    event.preventDefault();
    camera.orthogonalizeViewUp();
    handle.renderer.resetCameraClippingRange();
    handle.renderWindow.render();
  }

  return <div ref={container} tabIndex={0} role="application" onKeyDown={onKeyDown}
    aria-label={props.label || '3D view. Drag to orbit, right-drag or shift-drag to pan, scroll to zoom, arrow keys orbit, plus and minus zoom.'}
    className="absolute inset-0 outline-none [&>canvas]:outline-none" />;
});

// ---- helpers (module scope; they operate on the handle) -----------------------------------------
function addActor(handle: any, poly: any, style: (actor: any) => void) {
  const mapper = vtkMapper.newInstance(); mapper.setInputData(poly); mapper.setScalarVisibility(false);
  const actor = vtkActor.newInstance(); actor.setMapper(mapper); actor.setPickable(false);
  style(actor);
  handle.renderer.addActor(actor);
  return { actor, mapper, poly };
}

function fitIfFirst(handle: any) {
  if (handle.fitted) return;
  handle.fitted = true;
  const camera = handle.renderer.getActiveCamera();
  const initial: CameraPreset | null | undefined = handle.propsRef?.current?.initialCamera;
  if (initial) {
    presetCamera(handle.renderer, initial);
  } else {
    camera.setPosition(0, 0, 1); camera.setFocalPoint(0, 0, 0); camera.setViewUp(0, 1, 0);
    handle.renderer.resetCamera();
    camera.azimuth(-35); camera.elevation(25); camera.setViewUp(0, 0, 1); camera.orthogonalizeViewUp();
  }
  handle.renderer.resetCamera();
  camera.zoom(0.82);
  handle.renderer.resetCameraClippingRange();
}

function recolor(handle: any, geometry: GeometryView | null | undefined) {
  const g = handle.geometry;
  if (!g || !geometry) return;
  const colors = geometry.colors || { neutral: [184, 196, 207], selected: [2, 187, 239], highlight: [252, 114, 83] };
  const selected = new Set(geometry.selectedGroups || []);
  const highlight = new Set(geometry.highlightGroups || []);
  const values = g.colors.getData() as Uint8Array;
  g.groups.forEach((group: number, i: number) => {
    const rgb = selected.has(group) ? colors.selected : highlight.has(group) ? colors.highlight : colors.neutral;
    values[i * 3] = rgb[0]; values[i * 3 + 1] = rgb[1]; values[i * 3 + 2] = rgb[2];
  });
  g.colors.modified();
  g.poly.modified();
}

function applyLayer(handle: any, layer: ViewLayer) {
  const entry = handle.layers.get(layer.id);
  if (!entry?.source) return;
  const every = Math.max(1, Math.round(layer.subsample || 1));
  if (entry.subsample !== every) {
    entry.poly = subsampleLines(entry.source, every);
    entry.mapper.setInputData(entry.poly);
    entry.subsample = every;
  }
  const { actor, mapper } = entry;
  const property = actor.getProperty();
  const info = layer.field ? arrayInfo(entry.poly, layer.field) : null;
  // Detach the old table before deleting it so the mapper never holds a deleted object.
  mapper.setLookupTable(null);
  entry.lut?.delete();
  entry.lut = null;
  if (info) {
    const range = layer.range && Number.isFinite(layer.range[0]) && Number.isFinite(layer.range[1]) ? layer.range : (info.array.getRange(info.array.getNumberOfComponents() > 1 ? -1 : 0) as [number, number]);
    entry.lut = lookupTable(layer.colormap || 'cool_to_warm', range, info.array.getNumberOfComponents() > 1);
    mapper.setLookupTable(entry.lut);
    mapper.setUseLookupTableScalarRange(true);
    mapper.setScalarModeToUsePointFieldData();
    if (info.association === 'cell') mapper.setScalarModeToUseCellFieldData();
    mapper.setColorByArrayName(layer.field);
    mapper.setColorModeToMapScalars();
    mapper.setScalarVisibility(true);
  } else {
    mapper.setScalarVisibility(false);
    property.setColor(...(layer.color || [0.75, 0.79, 0.84]));
  }
  property.setOpacity(layer.opacity ?? 1);
  property.setLineWidth(layer.lineWidth ?? 1);
  property.setEdgeVisibility(!!layer.edges);
  property.setEdgeColor(0.2, 0.25, 0.3);
  property.setAmbient(layer.ambient ?? 0.25);
  property.setDiffuse(0.85);
  property.setSpecular(0.15);
  actor.setPickable(layer.pickable !== false);
  actor.setVisibility(layer.visible !== false);
  // Translucent iso-surfaces render after opaque surfaces.
  actor.setForceTranslucent((layer.opacity ?? 1) < 1);
}

async function pick(handle: any, x: number, y: number): Promise<{ geometryGroup?: number; result?: ProbeResult } | null> {
  const selections = await handle.selector.selectAsync(handle.renderer, x, y, x, y);
  const node = selections?.[0];
  if (!node) return null;
  const properties = node.getProperties();
  const prop = properties.prop;
  const cellId = properties.attributeID;
  if (handle.geometry && prop === handle.geometry.actor) return { geometryGroup: handle.geometry.groups[cellId] };
  for (const [layerId, entry] of handle.layers) {
    if (entry.actor !== prop || !entry.poly) continue;
    const layer = (scene_props(handle)?.layers || []).find((l: ViewLayer) => l.id === layerId);
    const field = layer?.field || null;
    let value: number | null = null;
    let components: number[] | undefined;
    if (field) {
      const info = arrayInfo(entry.poly, field);
      if (info) {
        const data = info.array.getData();
        const n = info.array.getNumberOfComponents();
        if (info.association === 'cell') {
          components = Array.from({ length: n }, (_, c) => data[cellId * n + c]);
        } else {
          const ids = cellPointIds(entry.poly, cellId);
          if (ids.length) components = Array.from({ length: n }, (_, c) => ids.reduce((s, id) => s + data[id * n + c], 0) / ids.length);
        }
        if (components) value = n > 1 ? Math.hypot(...components) : components[0];
      }
    }
    const part = entry.poly.getCellData().getArrayByName('part_index');
    const position = properties.worldPosition ? (Array.from(properties.worldPosition) as Vec3) : null;
    const dpr = window.devicePixelRatio || 1;
    const height = handle.apiWindow.getSize()[1];
    return { result: { layerId, field, value, components, position, cellId, partIndex: part ? part.getData()[cellId] : null, screen: { x: x / dpr, y: (height - y) / dpr } } };
  }
  return null;
}

function scene_props(handle: any) { return handle.propsRef?.current; }

/** Per-dataset cell offsets. vtk.js objects are frozen, so the cache lives beside them (a
 *  property set on the dataset throws, which once made every probe value silently empty). */
const cellIndexes = new WeakMap<object, { data: ArrayLike<number>[]; which: Uint8Array; at: Uint32Array }>();

/** Point ids of one cell, read straight from the connectivity arrays. Cell ids run over verts,
 *  lines, polys and strips in that order (as the GPU selector numbers them). */
function cellPointIds(poly: any, cellId: number): number[] {
  let index = cellIndexes.get(poly);
  if (!index) {
    const data = [poly.getVerts?.(), poly.getLines?.(), poly.getPolys?.(), poly.getStrips?.()].map((cells) => (cells?.getData?.() || []) as ArrayLike<number>);
    const which: number[] = [], at: number[] = [];
    data.forEach((array, k) => { for (let i = 0; i < array.length; i += array[i] + 1) { which.push(k); at.push(i); } });
    index = { data, which: Uint8Array.from(which), at: Uint32Array.from(at) };
    cellIndexes.set(poly, index);
  }
  if (cellId < 0 || cellId >= index.at.length) return [];
  const array = index.data[index.which[cellId]], start = index.at[cellId];
  return Array.from({ length: array[start] }, (_, k) => array[start + 1 + k]);
}

function updateAnchors(handle: any, props: Props) {
  const width = handle.apiWindow.getSize()[0], height = handle.apiWindow.getSize()[1];
  const dpr = window.devicePixelRatio || 1;
  const project = (point: number[]) => {
    const [x, y, z] = handle.apiWindow.worldToDisplay(point[0], point[1], point[2], handle.renderer);
    return { x: x / dpr, y: (height - y) / dpr, visible: x >= 0 && x <= width && y >= 0 && y <= height && z < 1 };
  };
  if (props.onAnchors && props.anchors) {
    const positions: Record<string, AnchorPosition> = {};
    for (const anchor of props.anchors) positions[anchor.id] = project(anchor.point);
    props.onAnchors(positions);
  }
  if (props.onDimensionLabels) props.onDimensionLabels((handle.dimensionLabels || []).map((l: any) => ({ axis: l.axis, length: l.length, ...project(l.point) })));
}
