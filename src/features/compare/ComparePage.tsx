import { useState } from 'react';
import { useLocation, useSearch } from 'wouter';
import { GitCompareArrows } from 'lucide-react';
import { useCompare, useSimulations } from '../../api/queries';
import { BarList } from '../../charts/SeriesChart';
import { significant } from '../../lib/units';
import { Card, EmptyState, Notice, Spinner } from '../../ui';
import { CompareTable } from '../results/CompareTab';

export default function ComparePage() {
  const search = useSearch();
  const [, navigate] = useLocation();
  const ids = new URLSearchParams(search).get('runs')?.split(',').filter(Boolean) || [];
  const simulations = useSimulations();
  const done = (simulations.data || []).filter((s) => s.results_run_id);
  const compare = useCompare(ids);
  const [metric, setMetric] = useState('cd');
  const toggle = (runId: string) => {
    const next = ids.includes(runId) ? ids.filter((i) => i !== runId) : [...ids, runId].slice(0, 12);
    navigate(`/compare?runs=${next.join(',')}`, { replace: true });
  };
  const data = compare.data;
  const column = data?.columns.find((c) => c.id === metric) || data?.columns[0];
  return <div className="mx-auto w-full max-w-[1800px] space-y-4 px-4 py-6">
    <div className="flex items-center gap-2"><GitCompareArrows size={20} className="text-accent" /><h1 className="text-xl font-semibold">Compare simulations</h1></div>
    <Card title="Simulations">
      {done.length === 0 ? <p className="hint">Completed simulations appear here.</p> : <div className="flex flex-wrap gap-2">{done.map((s) => <label key={s.id} className="chip cursor-pointer gap-2 px-3 py-1.5 text-sm">
        <input type="checkbox" checked={ids.includes(s.results_run_id!)} onChange={() => toggle(s.results_run_id!)} />{s.name}</label>)}</div>}
    </Card>
    {ids.length < 2 ? <EmptyState icon={<GitCompareArrows size={22} />} title="Choose two or more simulations">Select completed simulations above. The first one is the baseline.</EmptyState>
      : compare.isLoading ? <Spinner /> : compare.error ? <Notice tone="bad">{String((compare.error as Error).message)}</Notice> : data && <>
        {data.warnings.map((w) => <Notice key={w.code} tone="warn">{w.detail}</Notice>)}
        <Card title="Results"><CompareTable data={data} /><p className="hint mt-3">{data.note}</p></Card>
        {column && <Card title="Chart" actions={<select className="input w-auto py-1.5 text-xs" aria-label="Quantity" value={column.id} onChange={(e) => setMetric(e.target.value)}>{data.columns.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</select>}>
          <BarList items={data.rows.map((r) => ({ label: r.name, value: r.values[column.id] ?? 0, highlight: r.run_id === data.baseline }))} unit={column.unit !== '1' ? column.unit : undefined} format={(v) => significant(v, 4)} />
        </Card>}
        {data.config_diff.length > 0 && <Card title="Setup differences"><div className="overflow-x-auto"><table className="table-clean text-xs"><thead><tr><th>Setting</th>{data.rows.map((r) => <th key={r.run_id}>{r.name}</th>)}</tr></thead>
          <tbody>{data.config_diff.slice(0, 80).map((d) => <tr key={d.path}><td className="font-mono">{d.path}</td>{d.values.map((v, i) => <td key={i}>{JSON.stringify(v)}</td>)}</tr>)}</tbody></table></div></Card>}
      </>}
  </div>;
}
