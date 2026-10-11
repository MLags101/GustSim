import { useState } from 'react';
import { Lightbulb } from 'lucide-react';
import type { Insight, Insights, Manifest, Run } from '../../api/types';
import { significant } from '../../lib/units';
import { Badge, EmptyState, cx } from '../../ui';
import { Legend } from '../../viewer/Legend';
import { ViewerFrame } from '../../viewer/ViewerFrame';
import type { AnchorPosition, ViewLayer } from '../../viewer/Viewport';

const TONE = { warning: 'bad', suggestion: 'warn', info: 'active' } as const;

export function InsightsTab({ run, manifest, insights }: { run: Run; manifest: Manifest | null; insights: Insights | null }) {
  const [active, setActive] = useState<Insight | null>(null);
  const [positions, setPositions] = useState<Record<string, AnchorPosition>>({});
  const [sync, setSync] = useState<any>(null);
  if (!insights) return <EmptyState icon={<Lightbulb size={22} />} title="No insights yet">Insights are generated with the results package.</EmptyState>;
  const items = insights.insights;
  const base = (path: string) => `/api/runs/${run.id}/files/${path}`;
  const layersById = Object.fromEntries((manifest?.layers || []).map((l) => [l.id, l]));
  const preset = active?.view_preset;
  const surfaceField = preset?.array && layersById.surface?.arrays.some((a) => a.name === preset.array) ? preset.array : 'Cp';
  const range: [number, number] = preset?.range || [-1, 1];
  const layers: ViewLayer[] = [];
  if (layersById.surface?.url) layers.push({ id: 'surface', url: base(layersById.surface.url), field: surfaceField, colormap: surfaceField === 'Cp' ? 'cool_to_warm' : 'rainbow', range, pickable: false });
  if (preset?.layers.includes('friction_lines') && layersById.friction_lines?.url) layers.push({ id: 'friction', url: base(layersById.friction_lines.url), color: [0.12, 0.16, 0.22], pickable: false, ambient: 1 });
  if (preset?.layers.includes('pressure_cloud') && layersById.pressure_cloud?.url) layers.push({ id: 'cloud', url: base(layersById.pressure_cloud.url), color: [0.86, 0.15, 0.2], opacity: 0.5, pickable: false });
  function choose(item: Insight) { setActive(item); setSync({ ...item.view_preset.camera }); }
  return <div className="grid overflow-hidden rounded-2xl border border-line bg-surface lg:h-[calc(100vh-15.5rem)] lg:min-h-[34rem] lg:grid-cols-[24rem_1fr]">
    <aside className="max-h-[50vh] overflow-y-auto border-b border-line scrollbar-thin lg:max-h-none lg:border-b-0 lg:border-r">
      <div className="border-b border-line px-4 py-3"><h3 className="text-sm font-semibold">Flow insights</h3><p className="hint mt-1">{insights.disclaimer}</p></div>
      {items.length === 0 && <p className="hint p-4">No rule flagged anything noteworthy in this solution.</p>}
      <ol className="divide-y divide-line">{items.map((item, i) => <li key={item.id}>
        <button className={cx('w-full px-4 py-3 text-left hover:bg-surface-2', active?.id === item.id && 'bg-accent-soft')} onClick={() => choose(item)}>
          <div className="flex items-center gap-2"><span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-indigo-brand text-[11px] font-bold text-white">{i + 1}</span>
            <span className="flex-1 text-sm font-semibold">{item.title}</span><Badge tone={TONE[item.severity]}>{item.severity}</Badge></div>
          <p className="mt-1 text-sm text-ink-2">{item.message}</p>
          {active?.id === item.id && <div className="mt-2 space-y-1">
            <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">{Object.entries(item.evidence).map(([k, e]) => <div key={k}><dt className="inline text-muted">{k.replaceAll('_', ' ')}: </dt><dd className="inline font-semibold tabular-nums">{e.unit === '1' && k.includes('share') ? `${(e.value * 100).toFixed(1)} %` : `${significant(e.value, 3)} ${e.unit !== '1' ? e.unit : ''}`}</dd></div>)}</dl>
            <p className="hint">{item.confidence === 'measured' ? 'Measured from this run' : 'Heuristic'} · rule {item.rule} · {item.limitations}</p>
          </div>}
        </button>
      </li>)}</ol>
    </aside>
    <ViewerFrame className="h-[60vh] lg:h-auto" layers={layers} bounds={manifest?.bounds.model} presets={manifest?.camera_presets} syncCamera={sync}
      anchors={items.map((item) => ({ id: item.id, point: item.anchor.point }))} onAnchors={setPositions}>
      {items.map((item, i) => { const p = positions[item.id]; if (!p?.visible) return null;
        return <button key={item.id} onClick={() => choose(item)} aria-label={`Insight ${i + 1}: ${item.title}`}
          className={cx('absolute grid h-7 w-7 -translate-x-1/2 -translate-y-1/2 place-items-center rounded-full border-2 border-white text-xs font-bold text-white shadow-lg transition-transform hover:scale-110',
            item.severity === 'warning' ? 'bg-danger' : item.severity === 'suggestion' ? 'bg-warning' : 'bg-indigo-brand', active?.id === item.id && 'scale-125 ring-4 ring-accent/40')}
          style={{ left: p.x, top: p.y }}>{i + 1}</button>; })}
      <div className="pointer-events-none absolute bottom-3 right-3"><Legend colormap={surfaceField === 'Cp' ? 'cool_to_warm' : 'rainbow'} range={range} label={surfaceField === 'Cp' ? 'Pressure (Cp)' : surfaceField} unit={surfaceField === 'Cp' || surfaceField === 'Cf' ? '1' : surfaceField === 'yPlus' ? '1' : 'Pa'} /></div>
    </ViewerFrame>
  </div>;
}
