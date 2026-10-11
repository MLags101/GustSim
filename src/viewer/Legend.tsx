import { COLORMAPS, cssGradient, niceTicks, type ColormapId } from '../lib/colormaps';
import { significant } from '../lib/units';

/** Colour legend sampled from the same colormap definition the viewer's lookup table uses. */
export function Legend({ colormap, range, label, unit, captions }: { colormap: ColormapId; range: [number, number]; label: string; unit?: string | null; captions?: [string, string] }) {
  const map = COLORMAPS[colormap];
  const [low, high] = range;
  const ticks = niceTicks(low, high, 5).filter((t) => t >= Math.min(low, high) && t <= Math.max(low, high));
  const span = high - low || 1;
  return <div className="pointer-events-auto w-64 rounded-xl border border-line bg-surface/95 px-3 py-2 shadow-sm backdrop-blur" role="img" aria-label={`Colour scale for ${label} from ${significant(low)} to ${significant(high)} ${unit || ''}`}>
    <div className="mb-1 flex items-baseline justify-between gap-2"><span className="truncate text-xs font-semibold text-ink">{label}</span>{unit && unit !== '1' && <span className="text-[11px] text-muted">{unit}</span>}</div>
    <div className="h-2.5 rounded-sm" style={{ background: cssGradient(map) }} />
    <div className="relative mt-0.5 h-4 text-[10px] tabular-nums text-muted">
      {ticks.map((t) => <span key={t} className="absolute -translate-x-1/2" style={{ left: `${((t - low) / span) * 100}%` }}>{significant(t, 3)}</span>)}
    </div>
    {captions && <div className="mt-0.5 flex justify-between text-[10px] font-medium text-muted"><span>{captions[0]}</span><span>{captions[1]}</span></div>}
  </div>;
}
