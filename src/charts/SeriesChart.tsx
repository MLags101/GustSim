import { useEffect, useRef, useState } from 'react';
import uPlot from 'uplot';
import { Table2 } from 'lucide-react';
import { cx } from '../ui';

export interface Series { name: string; values: [number, number][]; color?: string; dash?: number[] }
const PALETTE = ['#0ea5e9', '#4053c5', '#fc7253', '#16a34a', '#a855f7', '#eab308', '#14b8a6', '#ef4444'];
const DASHES = [[], [6, 3], [2, 3], [8, 3, 2, 3], [], [4, 4]];

function themeColor(name: string, fallback: string) {
  if (typeof window === 'undefined') return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

/** Merge series onto one sorted x axis (uPlot needs aligned arrays). */
function align(series: Series[]): uPlot.AlignedData {
  const xs = [...new Set(series.flatMap((s) => s.values.map((v) => v[0])))].sort((a, b) => a - b);
  const index = new Map(xs.map((x, i) => [x, i]));
  const ys = series.map((s) => {
    const column: (number | null)[] = new Array(xs.length).fill(null);
    for (const [x, y] of s.values) column[index.get(x)!] = Number.isFinite(y) ? y : null;
    return column;
  });
  return [xs, ...ys] as uPlot.AlignedData;
}

export function SeriesChart({ series, height = 220, log = false, xLabel = 'Iteration', yLabel, band, title, className }: {
  series: Series[]; height?: number; log?: boolean; xLabel?: string; yLabel?: string;
  band?: { start: number; end: number; label: string } | null; title?: string; className?: string;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<uPlot | null>(null);
  const [showTable, setShowTable] = useState(false);
  const [themeTick, setThemeTick] = useState(0);
  const names = series.map((s) => s.name).join('|');

  useEffect(() => {
    const observer = new MutationObserver(() => setThemeTick((v) => v + 1));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!host.current) return;
    const ink = themeColor('--muted', '#64748b');
    const grid = themeColor('--line', '#e2e8f0');
    const accentSoft = themeColor('--accent-soft', '#e0f2fe');
    const bandRef = { current: band };
    const options: uPlot.Options = {
      width: host.current.clientWidth || 600, height,
      scales: { x: { time: false }, y: log ? { distr: 3 } : { auto: true } },
      axes: [
        { stroke: ink, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, label: xLabel, labelSize: 18, font: '11px Figtree Variable, sans-serif', labelFont: '11px Figtree Variable, sans-serif' },
        { stroke: ink, grid: { stroke: grid, width: 1 }, ticks: { stroke: grid }, label: yLabel, labelSize: 18, size: 58, font: '11px Figtree Variable, sans-serif', labelFont: '11px Figtree Variable, sans-serif',
          values: (_u, values) => values.map((v) => (v == null ? '' : Math.abs(v) >= 1e4 || (Math.abs(v) < 1e-2 && v !== 0) ? v.toExponential(0) : String(Number(v.toPrecision(4))))) },
      ],
      series: [{ label: xLabel }, ...series.map((s, i) => ({ label: s.name, stroke: s.color || PALETTE[i % PALETTE.length], width: 1.6, dash: s.dash || DASHES[i % DASHES.length], spanGaps: true, points: { show: false } }))],
      legend: { show: true, live: true },
      cursor: { drag: { x: true, y: false } },
      hooks: {
        drawClear: [(u) => {
          const b = bandRef.current;
          if (!b) return;
          const left = u.valToPos(b.start, 'x', true), right = u.valToPos(b.end, 'x', true);
          const ctx = u.ctx;
          ctx.save();
          ctx.fillStyle = accentSoft;
          ctx.fillRect(left, u.bbox.top, Math.max(1, right - left), u.bbox.height);
          ctx.restore();
        }],
      },
    };
    chart.current?.destroy();
    chart.current = new uPlot(options, align(series), host.current);
    const resize = new ResizeObserver(() => { if (host.current && chart.current) chart.current.setSize({ width: host.current.clientWidth, height }); });
    resize.observe(host.current);
    return () => { resize.disconnect(); chart.current?.destroy(); chart.current = null; };
  }, [names, log, height, xLabel, yLabel, themeTick, band?.start, band?.end]);

  useEffect(() => { chart.current?.setData(align(series)); }, [series]);

  const rows = align(series);
  return <figure className={cx('min-w-0', className)}>
    <div className="mb-1 flex items-center justify-between gap-2">
      {title && <figcaption className="text-sm font-semibold">{title}</figcaption>}
      <button className="btn-ghost btn-sm ml-auto" onClick={() => setShowTable((v) => !v)} aria-pressed={showTable}><Table2 size={13} />{showTable ? 'Chart' : 'Data'}</button>
    </div>
    <div ref={host} className={cx('w-full', showTable && 'hidden')} role="img" aria-label={`${title || 'Chart'}: ${series.map((s) => s.name).join(', ')} against ${xLabel}`} />
    {band && !showTable && <p className="hint mt-1">Shaded: {band.label}</p>}
    {showTable && <div className="max-h-64 overflow-auto rounded-lg border border-line"><table className="table-clean text-xs">
      <thead><tr><th>{xLabel}</th>{series.map((s) => <th key={s.name}>{s.name}</th>)}</tr></thead>
      <tbody>{(rows[0] as number[]).slice(-300).map((x, i, all) => { const k = (rows[0] as number[]).length - all.length + i; return <tr key={x}><td>{x}</td>{series.map((s, j) => <td key={s.name}>{(rows[j + 1] as (number | null)[])[k]?.toPrecision(5) ?? '—'}</td>)}</tr>; })}</tbody>
    </table></div>}
  </figure>;
}

/** Horizontal bars with an optional second segment (e.g. pressure and friction parts). */
export function BarList({ items, unit, format }: { items: { label: string; value: number; split?: [number, number]; highlight?: boolean }[]; unit?: string; format: (v: number) => string }) {
  const max = Math.max(...items.map((i) => Math.abs(i.value)), 1e-12);
  return <ul className="space-y-2">{items.map((item) => <li key={item.label}>
    <div className="mb-0.5 flex justify-between gap-3 text-xs"><span className={cx('truncate', item.highlight ? 'font-semibold text-ink' : 'text-ink-2')}>{item.label}</span><span className="tabular-nums text-ink-2">{format(item.value)}{unit ? ` ${unit}` : ''}</span></div>
    <div className="flex h-2.5 overflow-hidden rounded-full bg-surface-3">
      {item.split ? <><div className="h-full bg-indigo-brand" style={{ width: `${(Math.max(0, item.split[0]) / max) * 100}%` }} title="Pressure" /><div className="h-full bg-brand" style={{ width: `${(Math.max(0, item.split[1]) / max) * 100}%` }} title="Friction" /></>
        : <div className={cx('h-full', item.value < 0 ? 'bg-success' : 'bg-indigo-brand')} style={{ width: `${(Math.abs(item.value) / max) * 100}%` }} />}
    </div>
  </li>)}</ul>;
}
