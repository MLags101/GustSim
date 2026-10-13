import { useEffect, useState } from 'react';
import { ArrowLeftRight, RotateCcw, RotateCw, Wand2 } from 'lucide-react';
import { post } from '../../api/client';
import type { Vec3 } from '../../api/types';
import { significant } from '../../lib/units';
import { Field, QuantityField, Segmented, Spinner, VectorField } from '../../ui';
import type { SetupState } from './useSetup';

export interface RotorDetection {
  axis: Vec3; origin: Vec3; blade_radius: number; radius: number; length: number; blades: number | null; confidence: number; reasons: string[];
  /** +1: spun positively about `axis`, the blades push air towards -axis; 0 when unknown. */
  handedness?: number;
}

/** Mirrors rotors.orient: the axis points along the air the rotor pushes (the solver's inflow
 *  direction), so thrust, measured along -axis, comes out positive. The physical spin is kept. */
export function orient(axis: Vec3, rpm: number, sense?: number): { axis: Vec3; rpm: number } {
  if (sense && Math.sign(rpm) * sense > 0) return { axis: axis.map((v) => -v || 0) as Vec3, rpm: -rpm };
  return { axis, rpm };
}
const dot = (a: number[], b: number[]) => a.reduce((sum, v, i) => sum + v * b[i], 0);

const AXIS_NAMES: [Vec3, string][] = [[[1, 0, 0], '+X'], [[0, 1, 0], '+Y'], [[0, 0, 1], '+Z'], [[-1, 0, 0], '−X'], [[0, -1, 0], '−Y'], [[0, 0, -1], '−Z']];
export function axisName(axis: number[]) {
  const n = Math.hypot(...axis) || 1;
  const hit = AXIS_NAMES.find(([a]) => a.every((v, i) => Math.abs(v - axis[i]/n) < 1e-6));
  return hit ? hit[1] : `(${axis.map((v) => significant(v/n, 3)).join(', ')})`;
}

/** Detect the spin axis and centre of the selected rotating surfaces (a suggestion to confirm). */
export async function detectRotor(geometryId: string, patches: string[]) {
  return post<RotorDetection>(`/geometry/${geometryId}/rotor-detect`, { patches });
}

/** The main rotor: pick the part, GustSim finds the axis; speed as RPM plus a spin direction. */
export function RotorCard({ s }: { s: SetupState }) {
  const intent = s.intent!;
  const geometry = s.geometry.data!;
  const [detection, setDetection] = useState<RotorDetection | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const manual = intent.axis_mode === 'manual';
  const key = [...intent.patches].sort().join('|');

  async function detect(force = false) {
    if (!intent.patches.length) { setDetection(null); return; }
    setBusy(true); setError(null);
    try {
      const found = await detectRotor(geometry.id, intent.patches);
      setDetection(found);
      if (force || !manual) {
        // Keep the physical spin the user chose, expressed about the detected axis.
        const about = intent.rpm * (Math.sign(dot(intent.axis, found.axis)) || 1);
        const oriented = orient(found.axis, about, found.handedness);
        s.setIntent({ axis: oriented.axis, rpm: oriented.rpm, origin: found.origin, axis_mode: 'auto' });
      }
    } catch (e: any) { setError(String(e?.message || e)); setDetection(null); }
    finally { setBusy(false); }
  }
  useEffect(() => { void detect(); }, [geometry.id, key]);

  const speed = Math.abs(intent.rpm);
  // Spin is shown about a fixed reference (the detected axis), not the flow-oriented one.
  const reference = (detection?.axis || intent.axis) as Vec3;
  const toReference = Math.sign(dot(intent.axis, reference)) || 1;
  const sense = intent.rpm * toReference >= 0 ? 'ccw' : 'cw';
  const name = axisName(reference);
  const known = !manual && !!detection?.handedness;
  function setSpin(next: 'ccw' | 'cw', magnitude = speed) {
    const about = next === 'ccw' ? magnitude : -magnitude;
    if (known) s.setIntent({ ...orient(reference, about, detection!.handedness) });
    else s.setIntent({ rpm: about * toReference });
  }
  const pushed = axisName(intent.axis), thrust = axisName(intent.axis.map((v) => -v || 0));
  return <div className="space-y-3">
    <div className="rounded-xl border border-line bg-surface-2 p-3.5">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-semibold">Spin axis {name.replace('+', '±').replace('−', '±')}</span>
        {busy ? <Spinner label="Detecting" /> : detection && !manual && <span className="chip">detected · {Math.round(detection.confidence*100)}% confidence</span>}
        {manual && <span className="chip">set manually</span>}
        <span className="ml-auto flex gap-1">
          <button className="btn-ghost btn-sm" onClick={() => s.setIntent({ axis: intent.axis.map((v) => -v) as Vec3, rpm: -intent.rpm, axis_mode: 'manual' })}
            title="Reverse the axis direction; the spin stays the same physical direction"><ArrowLeftRight size={13} />Flip axis</button>
          <button className="btn-ghost btn-sm" disabled={busy || !intent.patches.length} onClick={() => detect(true)}><Wand2 size={13} />Detect again</button>
        </span>
      </div>
      {error ? <p className="mt-1 text-sm text-danger">{error}</p>
        : !intent.patches.length ? <p className="hint mt-1">Tick the propeller or fan under Model (or click it in the 3D view); the spin axis and centre are found from its shape.</p>
        : detection && <p className="hint mt-1">{detection.blades ? `${detection.blades} blades · ` : ''}blade radius {significant(detection.blade_radius, 3)} m · centre
          ({intent.origin.map((v) => significant(v, 3)).join(', ')}) m. {detection.reasons.join('; ')}.</p>}
      {intent.patches.length > 0 && <p className="mt-1.5 text-sm text-ink-2">{known
        ? <>With this spin the blades push air toward <b>{pushed}</b>; thrust is reported toward <b>{thrust}</b>.</>
        : <>Thrust is reported toward <b>{thrust}</b> (air pushed toward {pushed}). If the blade twist could not be read, flip the axis when thrust comes out negative.</>}</p>}
    </div>
    <div className="grid gap-3 sm:grid-cols-2">
      <QuantityField label="Rotational speed" quantity="rotation" value={speed} min={0} onChange={(v) => setSpin(sense, Math.abs(v))} />
      <Field label={`Spin direction, looking from the ${name} side`} hint={`Seen from ${name}, looking back at the rotor.`}>
        <Segmented label="Spin direction" value={sense} onChange={(v) => setSpin(v)} options={[
          { value: 'ccw', label: <span className="inline-flex items-center gap-1"><RotateCcw size={13} />Anticlockwise</span> },
          { value: 'cw', label: <span className="inline-flex items-center gap-1"><RotateCw size={13} />Clockwise</span> }]} />
      </Field>
    </div>
    <details open={manual}><summary className="cursor-pointer text-sm font-semibold text-ink-2">Set the axis manually</summary>
      <div className="mt-3 grid gap-3">
        <VectorField label="Rotation axis" value={intent.axis} onChange={(axis) => s.setIntent({ axis, axis_mode: 'manual' })} />
        <VectorField label="Rotation centre · m" value={intent.origin} onChange={(origin) => s.setIntent({ origin, axis_mode: 'manual' })} />
      </div>
    </details>
  </div>;
}
