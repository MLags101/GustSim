import { useMemo, useState } from 'react';
import { Eye, EyeOff, Focus, Search } from 'lucide-react';
import type { Geometry } from '../../api/types';
import { cx } from '../../ui';

interface Props {
  geometry: Geometry;
  selected: string[];
  onSelect: (patches: string[]) => void;
  hidden: string[];
  onHide: (patches: string[]) => void;
  selectLabel: string;
}

/** Parts (CAD components or surfaces) with select, hide and isolate. Selection is by surface
 *  patches; a component selects all of its surfaces. */
export function ComponentList({ geometry, selected, onSelect, hidden, onHide, selectLabel }: Props) {
  const [query, setQuery] = useState('');
  const [limit, setLimit] = useState(150);
  const parts = useMemo(() => {
    const list = geometry.parts?.length ? geometry.parts : geometry.patches.map((p) => ({ id: p.name, parent_id: null, name: p.name, patches: [p.name] }));
    const depth = (p: any) => { let d = 0, parent = p.parent_id; while (parent && d < 64) { d++; parent = list.find((q) => q.id === parent)?.parent_id; } return d; };
    return list.map((p) => ({ ...p, depth: depth(p) }));
  }, [geometry]);
  const filtered = parts.filter((p) => p.name.toLowerCase().includes(query.toLowerCase()));
  const all = geometry.patches.map((p) => p.name);
  return <div className="space-y-2">
    <div className="flex flex-wrap items-center gap-2">
      <div className="relative min-w-0 flex-1"><Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
        <input className="input py-1.5 pl-8" placeholder="Find a part" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Find a part" /></div>
      <button className="btn-ghost btn-sm" onClick={() => onSelect(all)}>Select all</button>
      <button className="btn-ghost btn-sm" onClick={() => onSelect([])}>None</button>
      {hidden.length > 0 && <button className="btn-ghost btn-sm" onClick={() => onHide([])}>Show all</button>}
    </div>
    <p className="hint">{selected.length} of {all.length} surfaces {selectLabel}. Click surfaces in the 3D view to toggle them.</p>
    <ul className="max-h-64 space-y-0.5 overflow-y-auto pr-1 scrollbar-thin">
      {filtered.slice(0, limit).map((p) => {
        const checked = p.patches.length > 0 && p.patches.every((n) => selected.includes(n));
        const partial = !checked && p.patches.some((n) => selected.includes(n));
        const isHidden = p.patches.length > 0 && p.patches.every((n) => hidden.includes(n));
        return <li key={p.id} className="group flex items-center gap-2 rounded-lg px-1.5 py-1 hover:bg-surface-3" style={{ paddingLeft: 6 + p.depth * 14 }}>
          <input type="checkbox" aria-label={`${selectLabel} ${p.name}`} checked={checked} ref={(el) => { if (el) el.indeterminate = partial; }}
            onChange={() => onSelect(checked ? selected.filter((n) => !p.patches.includes(n)) : [...new Set([...selected, ...p.patches])])} />
          <span className={cx('min-w-0 flex-1 truncate text-sm', isHidden && 'text-muted line-through')} title={p.name}>{p.name}</span>
          <span className="text-[11px] text-muted">{p.patches.length > 1 ? `${p.patches.length} surfaces` : ''}</span>
          <button className="rounded p-1 text-muted opacity-60 hover:text-ink group-hover:opacity-100" title={isHidden ? 'Show' : 'Hide'} aria-label={`${isHidden ? 'Show' : 'Hide'} ${p.name}`}
            onClick={() => onHide(isHidden ? hidden.filter((n) => !p.patches.includes(n)) : [...new Set([...hidden, ...p.patches])])}>{isHidden ? <EyeOff size={14} /> : <Eye size={14} />}</button>
          <button className="rounded p-1 text-muted opacity-60 hover:text-ink group-hover:opacity-100" title="Isolate" aria-label={`Isolate ${p.name}`}
            onClick={() => onHide(all.filter((n) => !p.patches.includes(n)))}><Focus size={14} /></button>
        </li>;
      })}
    </ul>
    {filtered.length > limit && <button className="btn-ghost btn-sm w-full" onClick={() => setLimit((v) => v + 150)}>Show more ({filtered.length - limit})</button>}
  </div>;
}
