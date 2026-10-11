import { useState } from 'react';
import type { Forces, Run, TransientStats } from '../../api/types';
import { useConvergence, useMonitor, useTransient } from '../../api/queries';
import { usePreferences } from '../../state/preferences';
import { formatQuantity, significant, toDisplay, UNITS } from '../../lib/units';
import { BarList, SeriesChart } from '../../charts/SeriesChart';
import { Card, EmptyState, Metric, Notice, Segmented, cx } from '../../ui';

const TYPICAL_CD: [string, number][] = [['Streamlined body', 0.04], ['Sports car', 0.3], ['Family car', 0.33], ['SUV', 0.4], ['Truck', 0.6], ['Upright cyclist', 0.9], ['Cube', 1.05], ['Flat plate', 1.28]];

function CdScale({ cd }: { cd: number }) {
  const pct = (v: number) => `${Math.min(100, (v / 1.5) * 100)}%`;
  return <div>
    <div className="relative mt-6 h-3 rounded-full bg-gradient-to-r from-success-soft via-warning-soft to-danger-soft">
      {TYPICAL_CD.map(([name, v]) => <span key={name} className="absolute top-0 h-3 w-px bg-line-strong" style={{ left: pct(v) }} title={`${name}: ${v}`} />)}
      <span className="absolute -top-6 -translate-x-1/2 rounded-md bg-accent px-1.5 py-0.5 text-[11px] font-bold text-white dark:text-slate-950" style={{ left: pct(cd) }}>{significant(cd, 3)}</span>
      <span className="absolute -top-1 h-5 w-1 -translate-x-1/2 rounded bg-accent" style={{ left: pct(cd) }} />
    </div>
    <div className="relative mt-1 h-10 text-[10px] text-muted">{TYPICAL_CD.filter((_, i) => i % 2 === 0 || i === TYPICAL_CD.length - 1).map(([name, v]) => <span key={name} className="absolute -translate-x-1/2 whitespace-nowrap text-center" style={{ left: pct(v) }}>{name}<br />{v}</span>)}</div>
    <p className="hint">Typical published values for orientation only; they are not GustSim results.</p>
  </div>;
}

const STATE_TONE: Record<string, 'good' | 'warn' | 'bad'> = { converged: 'good', review: 'warn', incomplete: 'warn', unavailable: 'bad' };

function TransientCard({ stats }: { stats: TransientStats }) {
  const units = usePreferences((s) => s.units);
  const history = stats.history;
  const cross = stats.cross_flow || 'lift_n';
  const spectrum = stats.spectra?.[cross];
  const coefficient = (key: string) => stats.statistics?.[key];
  const row = (name: string, key: string) => {
    const s = coefficient(key);
    return s ? <Metric key={key} label={name} value={`${significant(s.mean, 4)} ± ${significant(s.std, 2)}`} /> : null;
  };
  return <Card title="Transient loads">
    <Notice tone={STATE_TONE[stats.state] || 'warn'}>{stats.message}</Notice>
    <div className="mt-3 grid gap-3 sm:grid-cols-4">
      {row('Cd (mean ± std)', 'cd')}{row('Cl (mean ± std)', 'cl')}
      <Metric label="Strouhal number" value={stats.strouhal != null ? significant(stats.strouhal, 3) : '—'} />
      <Metric label="Dominant frequency" value={stats.dominant_frequency_hz != null ? `${significant(stats.dominant_frequency_hz, 3)} Hz` : '—'} />
    </div>
    <p className="hint mt-2">
      {significant(stats.plan.duration_s, 4)} s simulated ({significant(stats.plan.flow_throughs, 3)} domain flow-throughs, {significant(stats.plan.convective_times, 3)} convective times);
      averaged from {significant(stats.plan.average_start - stats.plan.start, 4)} s{stats.periods_in_window != null ? ` over ${significant(stats.periods_in_window, 3)} shedding periods` : ''}.
      St = f·L/U with the reference length.</p>
    {history && <SeriesChart xLabel="Time (s)" yLabel={UNITS.force[units.force]?.label || units.force} height={200}
      series={[{ name: 'drag', values: history.t_s.map((t, i) => [t, toDisplay('force', history.drag_n[i], units.force)] as [number, number]) },
        { name: 'lift', values: history.t_s.map((t, i) => [t, toDisplay('force', history.lift_n[i], units.force)] as [number, number]) }]}
      band={stats.window ? { start: stats.window.start_s, end: stats.window.end_s, label: 'averaging window' } : null} />}
    {spectrum && <SeriesChart xLabel="Frequency (Hz)" yLabel="PSD" log height={180}
      series={[{ name: `${cross.replace('_n', '')} force spectrum`, values: spectrum.frequency_hz.slice(1).map((f, i) => [f, Math.max(spectrum.psd[i + 1], 1e-30)] as [number, number]) }]} />}
  </Card>;
}

export function ForcesTab({ run, forces }: { run: Run; forces: Forces | null }) {
  const units = usePreferences((s) => s.units);
  const [frame, setFrame] = useState<'wind' | 'object'>('wind');
  const [mode, setMode] = useState<'components' | 'magnitude'>('components');
  const convergence = useConvergence(run.id);
  const transient = useTransient(run.id, run.spec?.time?.mode === 'transient');
  const monitor = useMonitor(run.id);
  const metrics = run.result?.metrics || {};
  const forceUnit = units.force;
  const F = (v: number | null | undefined) => formatQuantity('force', v ?? null, forceUnit);
  const label = UNITS.force[forceUnit]?.label || forceUnit;

  if (!forces) {
    if (metrics.drag_n == null && metrics.thrust_n == null) return <EmptyState title="No force results yet">Forces appear when the results package completes.</EmptyState>;
    return <div className="grid gap-3 sm:grid-cols-4">
      <Metric label="Drag" value={F(metrics.drag_n)} /><Metric label="Lift" value={F(metrics.lift_n)} />
      <Metric label="Cd" value={significant(metrics.cd, 4)} /><Metric label="Cl" value={significant(metrics.cl, 4)} />
    </div>;
  }
  const c = forces.coefficients;
  const block = forces.frames[frame].total;
  const axes = frame === 'wind' ? [['drag', 'Drag (Fx)'], ['side', 'Side (Fy)'], ['lift', 'Lift (Fz)']] : [['x', 'X'], ['y', 'Y'], ['z', 'Z']];
  const moments = [['roll', 'Roll (Mx)'], ['pitch', 'Pitch (My)'], ['yaw', 'Yaw (Mz)']];
  const magnitude = (rec: Record<string, number>) => Math.hypot(...Object.values(rec));
  const speedCurve = forces.speed_curve;
  // For a transient run the convergence card shows only the steady initialisation.
  const transientStart = transient.data?.plan.start;
  const forceHistory = (monitor.data?.forces || []).filter((r) => transientStart == null || r.iteration <= transientStart);
  const averaging = convergence.data?.averaging;
  const axisColor = ['text-axis-x', 'text-axis-y', 'text-axis-z'];
  return <div className="space-y-4">
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
      <Metric emphasis label="Cd" value={significant(c.cd, 4)} help="Drag coefficient: drag / (½ ρ V² A_ref)." />
      <Metric label="CdA" value={significant(c.cda_m2, 4)} unit="m²" help="Drag area, independent of the reference area: drag / (½ ρ V²)." />
      <Metric label="Cl" value={significant(c.cl, 4)} help="Lift coefficient; positive along the lift direction." />
      <Metric label="L/D" value={significant(c.l_over_d, 3)} />
      <Metric label="Drag" value={F(forces.frames.wind.total.force_n.drag)} />
      <Metric label="Lift" value={F(forces.frames.wind.total.force_n.lift)} />
      <Metric label="Cl front · rear" value={c.cl_front != null ? `${significant(c.cl_front, 3)} · ${significant(c.cl_rear, 3)}` : '—'} help={forces.lift_split ? `Split at ${forces.lift_split.basis === 'axles' ? 'the axles' : 'the model ends (no axles given)'}, wheelbase ${significant(forces.lift_split.wheelbase_m, 3)} m.` : undefined} />
      <Metric label="Frontal area" value={significant(forces.frontal_area_m2, 4)} unit="m²" />
    </div>
    {!forces.reference.confirmed && <Notice tone="warn">Coefficients are hidden because the reference area and length were not confirmed in the setup. Forces are still reported.</Notice>}
    {forces.blockage && forces.blockage.ratio > 0.05 && <Notice tone="warn">Blockage {(forces.blockage.ratio * 100).toFixed(1)} %: the virtual wind tunnel is narrow for this model. Corrected Cd ≈ {significant(forces.blockage.cd_corrected, 4)} ({forces.blockage.method}).</Notice>}
    <div className="grid gap-4 xl:grid-cols-[1.4fr_1fr]">
      <Card title="Forces and moments" actions={<div className="flex gap-2">
        <Segmented size="sm" label="Frame" value={frame} onChange={setFrame} options={[{ value: 'wind', label: 'Wind tunnel' }, { value: 'object', label: 'Object' }]} />
        <Segmented size="sm" label="Display" value={mode} onChange={setMode} options={[{ value: 'components', label: 'Components' }, { value: 'magnitude', label: 'Magnitude' }]} />
      </div>}>
        <table className="table-clean">
          <thead><tr><th>{frame === 'wind' ? 'Wind-tunnel axes' : 'Object axes'}</th><th className="text-right">Pressure</th><th className="text-right">Friction</th><th className="text-right">Total</th></tr></thead>
          <tbody>{mode === 'components' ? axes.map(([key, name], i) => <tr key={key}>
            <td className={cx('font-semibold', axisColor[i])}>{name}</td>
            <td className="text-right tabular-nums">{F(block.pressure_n[key])}</td><td className="text-right tabular-nums">{F(block.viscous_n[key])}</td><td className="text-right font-semibold tabular-nums">{F(block.force_n[key])}</td>
          </tr>) : <tr><td className="font-semibold">|F|</td><td className="text-right tabular-nums">{F(magnitude(block.pressure_n))}</td><td className="text-right tabular-nums">{F(magnitude(block.viscous_n))}</td><td className="text-right font-semibold tabular-nums">{F(magnitude(block.force_n))}</td></tr>}</tbody>
        </table>
        <table className="table-clean mt-3">
          <thead><tr><th>Moments about the reference origin</th><th className="text-right">Value</th></tr></thead>
          <tbody>{moments.map(([key, name], i) => <tr key={key}><td className={cx('font-semibold', axisColor[i])}>{name}</td><td className="text-right tabular-nums">{formatQuantity('moment', block.moment_nm[key], units.moment)}</td></tr>)}</tbody>
        </table>
        <p className="hint mt-2">{transient.data?.window
          ? `Averaged over the transient window t = ${significant(transient.data.window.start_s, 4)}–${significant(transient.data.window.end_s, 4)} s (time-accurate). Values in ${label}.`
          : `Averaged over iterations ${forces.averaging.start ?? '—'}–${forces.averaging.end ?? '—'} (${forces.averaging.basis.replace('_', ' ')}). Values in ${label}.`}</p>
      </Card>
      <Card title="Drag coefficient in context">{c.cd != null ? <CdScale cd={c.cd} /> : <p className="hint">Confirm the reference area to see Cd on the scale.</p>}</Card>
    </div>
    {forces.parts.length > 0 && <Card title="Drag by part">
      <BarList items={[...forces.parts].sort((a, b) => b.wind.drag_n - a.wind.drag_n).map((p) => ({ label: `${p.name}${p.share_of_drag != null ? ` · ${(p.share_of_drag * 100).toFixed(1)} %` : ''}`, value: toDisplay('force', p.wind.drag_n, forceUnit), split: [toDisplay('force', p.wind.pressure_drag_n, forceUnit), toDisplay('force', p.wind.viscous_drag_n, forceUnit)] as [number, number] }))}
        unit={label} format={(v) => significant(v, 4)} />
      <p className="hint mt-2"><span className="mr-1 inline-block h-2 w-2 rounded-sm bg-indigo-brand" />pressure <span className="mx-1 ml-3 inline-block h-2 w-2 rounded-sm bg-brand" />friction. {forces.parts_sum_check && !forces.parts_sum_check.ok ? forces.parts_sum_check.note : 'Parts sum to the total drag.'}</p>
    </Card>}
    <div className="grid gap-4 xl:grid-cols-2">
      {speedCurve && <Card title="Drag and power against speed">
        <SeriesChart xLabel={`Speed (${units.speed})`} yLabel={label} series={[{ name: `Drag (${label})`, values: speedCurve.points.map((p) => [toDisplay('speed', p.speed_m_s, units.speed), toDisplay('force', p.drag_n, forceUnit)] as [number, number]) }]} height={180} />
        <SeriesChart xLabel={`Speed (${units.speed})`} yLabel={units.power} series={[{ name: `Power (${units.power})`, values: speedCurve.points.map((p) => [toDisplay('speed', p.speed_m_s, units.speed), toDisplay('power', p.power_w, units.power)] as [number, number]) }]} height={180} />
        <p className="hint">{speedCurve.assumption}.</p>
      </Card>}
      {transient.data && <TransientCard stats={transient.data} />}
      <Card title={transient.data ? 'Steady initialisation' : 'Convergence'}>
        {convergence.data && <p className="mb-2 text-sm text-ink-2">{convergence.data.message}</p>}
        <SeriesChart yLabel="N" series={['drag_n', 'lift_n', 'thrust_n', 'force_n'].filter((k) => forceHistory.some((r) => r[k] != null)).map((k) => ({ name: k.replace('_n', ''), values: forceHistory.map((r) => [r.iteration, r[k]] as [number, number]) }))}
          band={averaging ? { start: averaging.start, end: averaging.end, label: 'averaging window used for the reported loads' } : null} height={220} />
      </Card>
    </div>
  </div>;
}
