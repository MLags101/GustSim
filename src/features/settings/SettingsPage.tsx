import { useCapabilities, useHealth } from '../../api/queries';
import { usePreferences, type Theme } from '../../state/preferences';
import { UNITS, type Quantity } from '../../lib/units';
import { Badge, Card, Segmented } from '../../ui';

const QUANTITY_LABEL: Record<Quantity, string> = { speed: 'Speed', length: 'Length', area: 'Area', force: 'Force', moment: 'Moment', power: 'Power', pressure: 'Pressure', flow: 'Volume flow', mass_flow: 'Mass flow', rotation: 'Rotation' };
const GATE_TONE = { passed: 'good', failed: 'bad', not_executed: 'neutral' } as const;

export default function SettingsPage() {
  const { theme, setTheme, units, setUnit, setTourCompleted } = usePreferences();
  const health = useHealth();
  const capabilities = useCapabilities();
  return <div className="mx-auto w-full max-w-5xl space-y-4 px-4 py-6">
    <h1 className="text-xl font-semibold">Settings &amp; about</h1>
    <Card title="Appearance"><Segmented label="Theme" value={theme} onChange={(t: Theme) => setTheme(t)} options={[{ value: 'light', label: 'Light' }, { value: 'dark', label: 'Dark' }, { value: 'system', label: 'System' }]} /></Card>
    <Card title="Display units"><p className="hint mb-3">GustSim stores and computes everything in SI units; these only change what is displayed.</p>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">{(Object.keys(UNITS) as Quantity[]).map((q) => <label key={q} className="block"><span className="label">{QUANTITY_LABEL[q]}</span>
        <select className="input" value={units[q]} onChange={(e) => setUnit(q, e.target.value)}>{Object.keys(UNITS[q]).map((u) => <option key={u} value={u}>{UNITS[q][u].label}</option>)}</select></label>)}</div>
    </Card>
    <Card title="Capabilities and validation status">
      <p className="hint mb-3">Core capabilities are supported; experimental ones run but stay labelled until their benchmark gates pass. A gate is a comparison against published measurements; “not executed” means it has not been run yet.</p>
      <div className="overflow-x-auto"><table className="table-clean min-w-[40rem]"><thead><tr><th>Capability</th><th>Maturity</th><th>Available here</th><th>Benchmark gates</th></tr></thead>
        <tbody>{Object.entries(capabilities.data?.physics || {}).map(([key, c]) => <tr key={key}>
          <td className="font-medium">{c.label}</td>
          <td><Badge tone={c.maturity === 'core' ? 'good' : c.maturity === 'experimental' ? 'experimental' : 'neutral'}>{c.maturity}</Badge></td>
          <td className="text-xs">{c.available === null ? <span className="text-muted">Worker not reporting</span> : c.available ? 'Yes' : <span title={c.reason || ''} className="text-muted">No</span>}</td>
          <td><div className="flex flex-wrap gap-1">{Object.entries(c.gates).map(([g, status]) => <Badge key={g} tone={GATE_TONE[status as keyof typeof GATE_TONE] || 'neutral'}>{g.replaceAll('_', ' ')}: {status.replace('_', ' ')}</Badge>)}</div></td>
        </tr>)}</tbody></table></div>
    </Card>
    <Card title="About GustSim">
      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">{[['Version', health.data?.version], ['Solver', `OpenFOAM (OpenCFD v${health.data?.openfoam_version || '2606'})`],
        ['Compute worker', health.data?.worker.online ? 'Online' : 'Offline'], ['Post-processing lane', health.data?.worker.lanes?.post?.online ? 'Online' : 'Offline'],
        ['CAD import worker', health.data?.worker.lanes?.cad?.online ? 'Online' : 'Offline (synchronous import is used)'], ['Data', 'Stays on this computer (Docker volume)']].map(([k, v]) =>
        <div key={k} className="flex justify-between gap-3 border-b border-line py-1"><dt className="text-muted">{k}</dt><dd className="font-medium">{v ?? '—'}</dd></div>)}</dl>
      <button className="btn-secondary btn-sm mt-3" onClick={() => setTourCompleted(false)}>Show the tour again on the next visit</button>
    </Card>
  </div>;
}
