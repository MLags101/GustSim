/** One wording for the evidence states everywhere (dashboard, results, compare, report).
 *  Nothing here can say "validated" unless the backend reports exactly that status. */
import type { StatusBlock } from '../api/types';

export type Tone = 'good' | 'warn' | 'bad' | 'neutral' | 'experimental' | 'active';
export interface EvidenceItem { key: string; label: string; value: string; tone: Tone; help: string }

const EXECUTION: Record<string, [string, Tone]> = {
  completed: ['Completed', 'good'], running: ['Running', 'active'], queued: ['Queued', 'active'], meshing: ['Meshing', 'active'],
  solving: ['Solving', 'active'], postprocessing: ['Post-processing', 'active'], awaiting_review: ['Mesh review', 'warn'],
  failed: ['Failed', 'bad'], cancelled: ['Cancelled', 'neutral'], interrupted: ['Interrupted', 'bad'], generating: ['Generating', 'active'],
};

export function validationLabel(status?: string): [string, Tone] {
  if (status === 'validated') return ['Validated', 'good'];
  return ['Pending', 'neutral'];
}

export function evidence(block: Partial<StatusBlock> | undefined): EvidenceItem[] {
  const b = block || {};
  const [execution, executionTone] = EXECUTION[b.execution || ''] || [b.execution || 'Not started', 'neutral'];
  const numerical = b.numerical === 'checks_passed' ? ['Checks passed', 'good'] : b.numerical === 'review_required' ? ['Review required', 'warn'] : ['Not available', 'neutral'];
  const [validation, validationTone] = validationLabel(b.validation);
  const items: EvidenceItem[] = [
    { key: 'geometry', label: 'Geometry', value: b.geometry === 'wrapped' ? 'Wrapped approximation' : b.geometry === 'morphed' ? 'Morphed shape' : 'Imported CAD', tone: b.geometry === 'imported' ? 'good' : 'warn',
      help: b.geometry === 'wrapped' ? 'Loads were computed on a shrink-wrapped approximation of the CAD.' : b.geometry === 'morphed' ? 'Loads were computed on a shape moved by the adjoint morpher. That prediction is not an experimental result.' : 'Loads were computed on the imported surface.' },
    { key: 'execution', label: 'Execution', value: execution, tone: executionTone, help: 'Whether the meshing and solver jobs ran to the end. A completed run is not a validated one.' },
    { key: 'numerical', label: 'Numerical quality', value: numerical[0], tone: numerical[1] as Tone, help: 'Mesh quality, residuals, mass balance and load convergence checks for this run.' },
    { key: 'validation', label: 'Experimental validation', value: validation, tone: validationTone, help: 'Comparison with measurements. Pending until the benchmark gates for this template pass.' },
  ];
  if (b.physics_maturity === 'experimental') {
    items.push({ key: 'maturity', label: 'Physics', value: 'Experimental', tone: 'experimental', help: 'At least one capability in this setup is experimental: results are labelled until its benchmark gate passes.' });
  }
  return items;
}

export const toneClass: Record<Tone, string> = {
  good: 'bg-success-soft text-success',
  warn: 'bg-warning-soft text-warning',
  bad: 'bg-danger-soft text-danger',
  neutral: 'bg-surface-3 text-muted',
  experimental: 'bg-experimental-soft text-experimental',
  active: 'bg-accent-soft text-accent-ink',
};
