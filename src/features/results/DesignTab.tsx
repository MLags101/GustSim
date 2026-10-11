import { Link } from 'wouter';
import { Spline } from 'lucide-react';
import { useDocument, useRun } from '../../api/queries';
import { significant } from '../../lib/units';
import { SeriesChart } from '../../charts/SeriesChart';
import { Card, EmptyState, ExperimentalBadge, Notice, Spinner } from '../../ui';

interface MorphRecord {
  run_id?: string; status?: string; objective?: string; history?: number[]; kept_cycle?: number | null;
  cycles_run?: number; geometry_id?: string | null; verification_id?: string | null; message?: string;
  stopped_because?: string; limitations?: string;
}
interface DesignDocument {
  objective: string; turbulence: string; available: boolean; limitations: string; convention: string;
  summary: { faces: number; min_n_per_m: number; max_n_per_m: number; mean_inward_delta_n_per_mm: number | null } | null;
  morph: MorphRecord | null;
}

export function DesignTab({ runId }: { runId: string }) {
  const design = useDocument<DesignDocument>(runId, 'design');
  const morphId = design.data?.morph?.run_id;
  const morphRun = useRun(morphId);
  if (design.isLoading) return <div className="grid place-items-center py-16"><Spinner label="Loading design advice" /></div>;
  if (design.isError || !design.data) return <EmptyState icon={<Spline size={22} />} title="Design advice is not available yet">The sensitivity map is written with the results package, after the adjoint finishes. The steady solution is kept either way.</EmptyState>;
  const data = design.data;
  const morph = data.morph;
  const history = morph?.history || [];
  const kept = morph?.kept_cycle;
  return <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
    <div className="flex items-center gap-2"><ExperimentalBadge /><span className="text-sm text-muted">{data.turbulence} adjoint · objective {data.objective}</span></div>
    <Notice tone="warn" title="A prediction, not a measurement">{data.limitations}</Notice>
    <Card title="Sensitivity map">
      {data.summary ? <dl className="grid gap-2 text-sm sm:grid-cols-3">
        <div><dt className="text-muted">Strongest inward</dt><dd className="font-semibold tabular-nums">{significant(data.summary.min_n_per_m, 4)} N/m</dd></div>
        <div><dt className="text-muted">Strongest outward</dt><dd className="font-semibold tabular-nums">{significant(data.summary.max_n_per_m, 4)} N/m</dd></div>
        <div><dt className="text-muted">Mean inward, per 1 mm</dt><dd className="font-semibold tabular-nums">{data.summary.mean_inward_delta_n_per_mm == null ? '—' : `${significant(data.summary.mean_inward_delta_n_per_mm, 3)} N`}</dd></div>
      </dl> : <p className="text-sm">The adjoint did not produce a sensitivity field. The steady solution is unchanged.</p>}
      <p className="hint mt-3">Open the Flow tab and colour the surface by Design advice. Blue is a predicted decrease for a 1 mm inward move; red is a predicted decrease for a 1 mm outward move. {data.convention}</p>
    </Card>
    {morph && <Card title="Shape morphing">
      <p className="text-sm">{morph.message || morphRun.data?.error || 'Morphing is a separate attempt and does not overwrite this solution.'}</p>
      {history.length > 1 && <div className="mt-3">
        <SeriesChart xLabel="Cycle" yLabel={`${data.objective} (N)`} height={200}
          series={[{ name: data.objective, values: history.map((y, i) => [i, y] as [number, number]) }]} />
        <p className="hint">Kept cycle {kept ?? '—'} of {history.length}. A line only joins the recorded objective; it does not interpolate a shape.</p>
      </div>}
      <p className="hint mt-2">{morph.stopped_because === 'objective rose' ? 'Stopped at the cycle before the objective rose.' : morph.stopped_because === 'cycle budget' ? 'Ran to the cycle budget.' : null} {morph.limitations}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {morph.verification_id && <Link href={`/simulations/${morph.verification_id}`} className="btn-secondary btn-sm">Open the morphed check</Link>}
        {morphRun.data?.status && <span className="chip">Morph attempt {morphRun.data.status}</span>}
      </div>
    </Card>}
  </div>;
}
