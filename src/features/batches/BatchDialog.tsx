import { useMemo, useState } from 'react';
import { Plus, Trash2 } from 'lucide-react';
import type { StudyPath } from '../../api/types';
import { usePreferences } from '../../state/preferences';
import { UNITS, fromDisplay, significant, toDisplay, type Quantity } from '../../lib/units';
import { Dialog, Notice, cx, toast } from '../../ui';
import type { SetupState } from '../setup/useSetup';

const MAX_MEMBERS = 30;
const TIERS = ['basic', 'regular', 'advanced'];

interface Parameter { path: StudyPath; label: string; quantity?: Quantity; unit?: string; sharesMesh: boolean; available: (spec: any) => boolean; current: (spec: any) => number | string }
/** Whitelisted batch paths (backend StudyPath). Yaw and geometry swaps are not offered here. */
export const PARAMETERS: Parameter[] = [
  { path: 'flow.speed', label: 'Speed', quantity: 'speed', sharesMesh: true, available: () => true, current: (s) => s.flow.speed },
  { path: 'flow.alpha_deg', label: 'Angle of attack', unit: '°', sharesMesh: false, available: (s) => !s.environment || s.environment.ground === 'none', current: (s) => s.flow.alpha_deg },
  { path: 'flow.beta_deg', label: 'Side-slip angle', unit: '°', sharesMesh: false, available: () => true, current: (s) => s.flow.beta_deg },
  { path: 'environment.ride_height_m', label: 'Ride height', quantity: 'length', sharesMesh: false, available: (s) => !!s.environment && s.environment.ground !== 'none', current: (s) => s.environment.ride_height_m },
  { path: 'rotation.rpm', label: 'Rotor speed', unit: 'RPM', sharesMesh: true, available: (s) => !!s.rotation?.enabled, current: (s) => s.rotation.rpm },
  { path: 'mesh.tier', label: 'Accuracy', sharesMesh: false, available: () => true, current: (s) => s.mesh.tier },
];

interface Row { path: StudyPath; text: string; tiers: string[] }

/** Parse "10, 15, 20" (display units) into SI numbers; null when any entry is not a number. */
export function parseValues(text: string, quantity: Quantity | undefined, unit: string | undefined): number[] | null {
  const parts = text.split(/[\s,;]+/).map((p) => p.trim()).filter(Boolean);
  if (!parts.length) return null;
  const numbers = parts.map(Number);
  if (numbers.some((n) => !Number.isFinite(n))) return null;
  const si = quantity && unit ? numbers.map((n) => fromDisplay(quantity, n, unit)) : numbers;
  return [...new Set(si)];
}

export function BatchDialog({ s, onClose, onLaunched }: { s: SetupState; onClose: () => void; onLaunched: (id: string) => void }) {
  const spec: any = s.spec;
  const units = usePreferences((u) => u.units);
  const options = PARAMETERS.filter((p) => p.available(spec));
  const speedUnit = units.speed;
  const [name, setName] = useState(`${spec?.name || 'Simulation'} · batch`);
  const [rows, setRows] = useState<Row[]>(() => [{ path: 'flow.speed', tiers: [],
    text: [0.75, 1, 1.25].map((f) => significant(toDisplay('speed', spec.flow.speed*f, speedUnit), 3)).join(', ') }]);
  const [busy, setBusy] = useState(false);

  const parsed = useMemo(() => rows.map((row) => {
    const parameter = PARAMETERS.find((p) => p.path === row.path)!;
    const unit = parameter.quantity ? units[parameter.quantity] : undefined;
    const values = row.path === 'mesh.tier' ? (row.tiers.length ? row.tiers : null) : parseValues(row.text, parameter.quantity, unit);
    return { row, parameter, values };
  }), [rows, units]);
  const count = parsed.reduce((n, p) => n*(p.values?.length || 0), 1);
  const invalid = parsed.some((p) => !p.values) || new Set(rows.map((r) => r.path)).size !== rows.length;
  const sharesMesh = parsed.every((p) => p.parameter.sharesMesh);
  const tooMany = count > MAX_MEMBERS;

  const set = (i: number, change: Partial<Row>) => setRows(rows.map((r, j) => (j === i ? { ...r, ...change } : r)));
  const unused = options.filter((o) => !rows.some((r) => r.path === o.path));

  async function launch() {
    setBusy(true);
    try {
      const batch = await s.launchBatch(name.trim() || 'Batch', parsed.map((p) => ({ path: p.row.path, values: p.values! })));
      onLaunched(batch.id);
    } catch (e) { toast.error(e); setBusy(false); }
  }

  return <Dialog open onOpenChange={(open) => { if (!open) onClose(); }} title="Launch a batch"
    description="Run several simulations from this setup, varying one parameter (a sweep) or several (a grid). Each member is an ordinary simulation with its own results.">
    <div className="space-y-4">
      <label className="block text-sm"><span className="label">Batch name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} /></label>
      <div className="space-y-3">
        {parsed.map(({ row, parameter, values }, i) => {
          const unit = parameter.quantity ? UNITS[parameter.quantity][units[parameter.quantity]]?.label : parameter.unit;
          return <div key={i} className="space-y-2 rounded-xl border border-line p-3">
            <div className="flex items-center gap-2">
              <select className="input w-auto py-1.5 text-sm" aria-label={`Parameter ${i + 1}`} value={row.path}
                onChange={(e) => set(i, { path: e.target.value as StudyPath, text: '', tiers: [] })}>
                {options.filter((o) => o.path === row.path || !rows.some((r) => r.path === o.path)).map((o) => <option key={o.path} value={o.path}>{o.label}</option>)}
              </select>
              <span className="hint">now {typeof parameter.current(spec) === 'number' && parameter.quantity
                ? `${significant(toDisplay(parameter.quantity, parameter.current(spec) as number, units[parameter.quantity]), 4)} ${unit}`
                : `${parameter.current(spec)}${parameter.unit && typeof parameter.current(spec) === 'number' ? ` ${parameter.unit}` : ''}`}</span>
              {rows.length > 1 && <button className="btn-ghost btn-icon ml-auto" aria-label="Remove parameter" onClick={() => setRows(rows.filter((_, j) => j !== i))}><Trash2 size={15} /></button>}
            </div>
            {row.path === 'mesh.tier'
              ? <div className="flex flex-wrap gap-1.5" role="group" aria-label="Accuracy tiers">{TIERS.map((tier) => <button key={tier} type="button"
                  className={cx('btn-secondary btn-sm capitalize', row.tiers.includes(tier) && 'border-accent bg-accent-soft text-accent-ink')}
                  aria-pressed={row.tiers.includes(tier)} onClick={() => set(i, { tiers: row.tiers.includes(tier) ? row.tiers.filter((t) => t !== tier) : [...row.tiers, tier] })}>{tier}</button>)}</div>
              : <label className="block text-sm"><span className="sr-only">{parameter.label} values</span>
                  <input className={cx('input', !values && row.text && 'border-danger')} placeholder={`Values in ${unit || 'SI'}, separated by commas`}
                    aria-label={`${parameter.label} values (${unit || 'SI'})`} value={row.text} onChange={(e) => set(i, { text: e.target.value })} /></label>}
          </div>;
        })}
        {unused.length > 0 && rows.length < 3 && <button className="btn-ghost btn-sm" onClick={() => setRows([...rows, { path: unused[0].path, text: '', tiers: [] }])}><Plus size={14} />Add a parameter (grid)</button>}
      </div>
      <p className="text-sm"><span className="font-semibold">{invalid ? '—' : count}</span> simulation{count === 1 ? '' : 's'} · {rows.length === 1 ? 'sweep' : 'grid of every combination'}</p>
      {tooMany && <Notice tone="bad">A batch is limited to {MAX_MEMBERS} simulations.</Notice>}
      <p className="hint">{sharesMesh
        ? 'Every member meshes identically, so they share one mesh: it is built and checked once, then each member solves on it.'
        : 'Angles, ride height and accuracy change the mesh, so those members mesh separately; members that differ only in speed or rotor speed still share a mesh.'}
        {' '}The setup's review findings{s.reviewNeeded ? ' (acknowledged above)' : ''} apply to every member.</p>
      <div className="flex justify-end gap-2">
        <button className="btn-secondary" onClick={onClose}>Cancel</button>
        <button className="btn-primary" disabled={invalid || tooMany || busy || count < 1} onClick={launch}>{busy ? 'Starting…' : `Launch ${invalid ? '' : count} simulations`}</button>
      </div>
    </div>
  </Dialog>;
}
