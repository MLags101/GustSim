import { forwardRef, useRef, useState, type ReactNode } from 'react';
import { Camera, Maximize2 } from 'lucide-react';
import type { CameraPreset } from '../api/types';
import { Viewport, type ViewportHandle } from './Viewport';
import { flowCamera, PRESET_LABELS, useViewerTheme, type PresetName } from './theme';
import { Spinner, cx, toast } from '../ui';

type ViewportProps = Omit<React.ComponentProps<typeof Viewport>, 'background' | 'cameraRequest'>;

/** Viewer chrome shared by setup, results and compare: dotted canvas, camera presets
 *  relative to the flow, fit, screenshot and slots for legends and annotations. */
export const ViewerFrame = forwardRef<ViewportHandle, ViewportProps & {
  bounds?: number[][] | null; flow?: number[]; lift?: number[]; className?: string; children?: ReactNode; toolbar?: ReactNode;
  screenshotName?: string; onScreenshot?: (dataUrl: string) => Promise<string> | string; loading?: boolean; error?: string | null;
  presets?: Record<string, CameraPreset> | null;
}>(function ViewerFrame({ bounds, flow, lift, className, children, toolbar, screenshotName = 'gustsim-view.png', onScreenshot, loading, error, presets, ...viewport }, ref) {
  const theme = useViewerTheme();
  const inner = useRef<ViewportHandle | null>(null);
  const [request, setRequest] = useState<{ camera?: CameraPreset | null; fit?: boolean; nonce: number } | null>(null);
  const [active, setActive] = useState<PresetName>('iso');
  const setRef = (handle: ViewportHandle | null) => {
    inner.current = handle;
    if (typeof ref === 'function') ref(handle); else if (ref) (ref as React.MutableRefObject<ViewportHandle | null>).current = handle;
  };
  function preset(name: PresetName) {
    setActive(name);
    const camera = presets?.[name] || (bounds ? flowCamera(name, bounds, flow, lift) : null);
    setRequest({ camera, nonce: Date.now() });
  }
  async function screenshot() {
    try {
      const raw = await inner.current!.capture();
      const image = onScreenshot ? await onScreenshot(raw) : raw;
      const a = document.createElement('a');
      a.href = image; a.download = screenshotName; a.click();
    } catch (e) { toast.error(e); }
  }
  const keys: Record<string, PresetName> = { '1': 'iso', '2': 'front', '3': 'rear', '4': 'left', '5': 'right', '6': 'top', '7': 'bottom' };
  function onKey(event: React.KeyboardEvent) {
    if (event.metaKey || event.ctrlKey || event.altKey) return;
    const name = keys[event.key];
    if (name) { event.preventDefault(); preset(name); }
    else if (event.key === 'f' || event.key === 'F') { event.preventDefault(); setRequest({ fit: true, nonce: Date.now() }); }
    else if (event.key === 's' || event.key === 'S') { event.preventDefault(); void screenshot(); }
  }
  return <div className={cx('dotted-grid relative overflow-hidden outline-none focus-visible:ring-2 focus-visible:ring-accent', className)} tabIndex={0} onKeyDown={onKey}
    aria-keyshortcuts="1 2 3 4 5 6 7 f s" aria-label="3D view. When focused: 1 isometric, 2 front, 3 rear, 4 left, 5 right, 6 top, 7 bottom, F fits, S saves a screenshot.">
    <Viewport ref={setRef} background={theme.background} cameraRequest={request} initialCamera={presets?.iso || (bounds ? flowCamera('iso', bounds, flow, lift) : null)} {...viewport} />
    <div className="pointer-events-none absolute inset-x-3 top-3 flex flex-wrap items-start justify-between gap-2">
      <div className="pointer-events-auto flex flex-wrap gap-1 rounded-xl border border-line bg-surface/90 p-1 shadow-sm backdrop-blur" role="toolbar" aria-label="Camera views" data-tour="camera-presets">
        {(Object.keys(PRESET_LABELS) as PresetName[]).map((name) => <button key={name} className={cx('rounded-lg px-2 py-1 text-xs font-semibold', active === name ? 'bg-accent-soft text-accent-ink' : 'text-muted hover:text-ink')} onClick={() => preset(name)}>{PRESET_LABELS[name]}</button>)}
        <button className="rounded-lg px-2 py-1 text-muted hover:text-ink" title="Fit to view" aria-label="Fit to view" onClick={() => setRequest({ fit: true, nonce: Date.now() })}><Maximize2 size={14} /></button>
        <button className="rounded-lg px-2 py-1 text-muted hover:text-ink" title="Save a screenshot (PNG)" aria-label="Save a screenshot" onClick={screenshot}><Camera size={14} /></button>
      </div>
      {toolbar && <div className="pointer-events-auto">{toolbar}</div>}
    </div>
    {children}
    {loading && <div className="pointer-events-none absolute bottom-3 right-3 rounded-lg bg-surface/90 px-3 py-1.5 shadow-sm"><Spinner label="Loading view" /></div>}
    {error && <div className="absolute inset-x-3 bottom-3 rounded-lg bg-danger-soft px-3 py-2 text-sm text-danger" role="alert">{error}</div>}
  </div>;
});
