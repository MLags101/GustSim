import * as RadixDialog from '@radix-ui/react-dialog';
import * as RadixTooltip from '@radix-ui/react-tooltip';
import { create } from 'zustand';
import { useEffect, useId, useState, type ReactNode } from 'react';
import { CircleAlert, CircleCheck, FlaskConical, Info, Loader2, X } from 'lucide-react';
import { evidence, toneClass, type Tone } from '../lib/evidence';
import { UNITS, fromDisplay, significant, toDisplay, type Quantity } from '../lib/units';
import { usePreferences } from '../state/preferences';
import type { StatusBlock } from '../api/types';

export const cx = (...names: (string | false | null | undefined)[]) => names.filter(Boolean).join(' ');

export function Card({ children, className, title, actions, pad = true, id }: { children: ReactNode; className?: string; title?: ReactNode; actions?: ReactNode; pad?: boolean; id?: string }) {
  return <section id={id} className={cx('card', className)}>
    {(title || actions) && <header className="flex items-center justify-between gap-3 border-b border-line px-4 py-3 sm:px-5">
      {typeof title === 'string' ? <h2 className="text-sm font-semibold">{title}</h2> : title}{actions}
    </header>}
    <div className={pad ? 'card-pad' : ''}>{children}</div>
  </section>;
}

export function Badge({ tone = 'neutral', children, title, className }: { tone?: Tone; children: ReactNode; title?: string; className?: string }) {
  return <span title={title} className={cx('inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold', toneClass[tone], className)}>{children}</span>;
}

export function ExperimentalBadge({ label = 'Experimental' }: { label?: string }) {
  return <Badge tone="experimental" title="Results from experimental capabilities stay labelled until their benchmark gates pass."><FlaskConical size={12} />{label}</Badge>;
}

export function EvidenceStrip({ block, compact = false }: { block?: Partial<StatusBlock>; compact?: boolean }) {
  const items = evidence(block);
  return <dl className={cx('flex flex-wrap gap-2', compact && 'gap-1')} aria-label="Evidence states">
    {items.map((item) => <Tip key={item.key} content={item.help}>
      <div className={cx('rounded-lg px-2.5 py-1', toneClass[item.tone], compact && 'px-2 py-0.5')}>
        {!compact && <dt className="text-[10px] font-semibold uppercase tracking-wide opacity-80">{item.label}</dt>}
        <dd className="text-xs font-semibold">{compact ? `${item.label}: ${item.value}` : item.value}</dd>
      </div>
    </Tip>)}
  </dl>;
}

export function Segmented<T extends string>({ value, options, onChange, label, size = 'md' }: { value: T; options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[]; onChange: (value: T) => void; label: string; size?: 'sm' | 'md' }) {
  return <div className="seg" role="radiogroup" aria-label={label}>
    {options.map((option) => <button key={option.value} type="button" role="radio" aria-checked={value === option.value} data-active={value === option.value}
      title={option.title} disabled={option.disabled} onClick={() => onChange(option.value)}
      className={cx('seg-item disabled:opacity-40', size === 'sm' && 'px-2 py-1 text-xs')}>{option.label}</button>)}
  </div>;
}

/** Viewer field pills (surface or volume), floating over the 3D view. */
export function Pills<T extends string>({ value, options, onChange, label }: { value: T; options: { value: T; label: ReactNode; disabled?: boolean; title?: string }[]; onChange: (value: T) => void; label: string }) {
  return <div className="flex max-w-full items-center gap-0.5 overflow-x-auto rounded-full bg-[var(--pill-bar)] p-1 shadow-sm backdrop-blur scrollbar-thin" role="radiogroup" aria-label={label}>
    <span className="shrink-0 px-2 text-[10px] font-semibold uppercase tracking-wider text-ink-2/70" aria-hidden>{label}</span>
    {options.map((option) => <button key={option.value} type="button" role="radio" aria-checked={value === option.value} title={option.title} disabled={option.disabled}
      onClick={() => onChange(option.value)}
      className={cx('shrink-0 whitespace-nowrap rounded-full px-3 py-1 text-xs font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-40',
        value === option.value ? 'bg-surface text-accent-ink shadow-sm ring-1 ring-accent/50' : 'text-ink-2 hover:bg-surface/70')}>{option.label}</button>)}
  </div>;
}

/** A card floating over the full-bleed 3D view. */
export function FloatCard({ title, right, children, className }: { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={cx('rounded-xl border border-line bg-surface/95 shadow-[var(--shadow-card)] backdrop-blur', className)}>
    {(title || right) && <header className="flex items-center justify-between gap-2 px-3.5 pt-3">{typeof title === 'string' ? <h3 className="text-[13px] font-semibold">{title}</h3> : title}{right}</header>}
    <div className="space-y-2.5 px-3.5 pb-3.5 pt-2.5">{children}</div>
  </section>;
}

export function Field({ label, children, hint, id }: { label: ReactNode; children: ReactNode; hint?: ReactNode; id?: string }) {
  return <div className="space-y-1" id={id}><label className="label">{label}</label>{children}{hint && <p className="hint">{hint}</p>}</div>;
}

/** Number input that keeps the typed text while editing and commits finite values. */
export function NumberInput({ value, onChange, step = 'any', min, max, ariaLabel, className }: { value: number | null | undefined; onChange: (v: number) => void; step?: number | 'any'; min?: number; max?: number; ariaLabel?: string; className?: string }) {
  const [draft, setDraft] = useState<string | null>(null);
  const shown = draft ?? (value === null || value === undefined || !Number.isFinite(value) ? '' : String(Number(Number(value).toPrecision(10))));
  return <input className={cx('input', className)} type="number" step={step} min={min} max={max} aria-label={ariaLabel} value={shown}
    onChange={(e) => { setDraft(e.target.value); const n = e.target.valueAsNumber; if (e.target.value !== '' && Number.isFinite(n)) onChange(n); }}
    onBlur={() => setDraft(null)} />;
}

export function NumberField({ label, value, onChange, hint, min, max, step, unit }: { label: string; value: number; onChange: (v: number) => void; hint?: ReactNode; min?: number; max?: number; step?: number | 'any'; unit?: string }) {
  return <Field label={label} hint={hint}><div className="flex items-center gap-2"><NumberInput value={value} onChange={onChange} min={min} max={max} step={step} ariaLabel={label} />{unit && <span className="text-xs text-muted">{unit}</span>}</div></Field>;
}

/** SI value shown and edited in the user's display unit, with an inline unit switch. */
export function QuantityField({ label, quantity, value, onChange, hint, min }: { label: string; quantity: Quantity; value: number; onChange: (si: number) => void; hint?: ReactNode; min?: number }) {
  const unit = usePreferences((s) => s.units[quantity]);
  const setUnit = usePreferences((s) => s.setUnit);
  const id = useId();
  return <Field label={label} hint={hint}><div className="flex gap-2">
    <NumberInput value={toDisplay(quantity, value, unit)} onChange={(v) => onChange(fromDisplay(quantity, v, unit))} min={min !== undefined ? toDisplay(quantity, min, unit) : undefined} ariaLabel={label} />
    <select id={id} aria-label={`${label} unit`} className="input w-auto" value={unit} onChange={(e) => setUnit(quantity, e.target.value)}>
      {Object.keys(UNITS[quantity]).map((u) => <option key={u} value={u}>{UNITS[quantity][u].label}</option>)}
    </select>
  </div></Field>;
}

export function VectorField({ label, value, onChange, hint }: { label: string; value: number[]; onChange: (v: [number, number, number]) => void; hint?: ReactNode }) {
  const v = (value?.length === 3 ? value : [0, 0, 0]) as [number, number, number];
  return <Field label={label} hint={hint}><div className="grid grid-cols-3 gap-2">
    {v.map((n, i) => <div key={i} className="relative">
      <span className={cx('pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-[10px] font-bold', ['text-axis-x', 'text-axis-y', 'text-axis-z'][i])}>{'XYZ'[i]}</span>
      <NumberInput className="pl-6" value={n} ariaLabel={`${label} ${'XYZ'[i]}`} onChange={(x) => onChange(v.map((m, j) => (j === i ? x : m)) as [number, number, number])} />
    </div>)}
  </div></Field>;
}

export function SelectField<T extends string>({ label, value, options, onChange, hint }: { label: string; value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; hint?: ReactNode }) {
  return <Field label={label} hint={hint}><select className="input" aria-label={label} value={value} onChange={(e) => onChange(e.target.value as T)}>
    {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
  </select></Field>;
}

export function Check({ checked, onChange, children, disabled }: { checked: boolean; onChange: (v: boolean) => void; children: ReactNode; disabled?: boolean }) {
  return <label className={cx('flex cursor-pointer items-start gap-2.5 rounded-lg p-1 text-sm text-ink-2', disabled && 'cursor-not-allowed opacity-50')}>
    <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[var(--accent)]" checked={checked} disabled={disabled} onChange={(e) => onChange(e.target.checked)} />
    <span>{children}</span>
  </label>;
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
    className={cx('relative inline-flex h-5 w-9 shrink-0 rounded-full transition-colors', checked ? 'bg-accent' : 'bg-line-strong')}>
    <span className={cx('absolute top-0.5 h-4 w-4 rounded-full bg-white shadow transition-transform', checked ? 'translate-x-4' : 'translate-x-0.5')} />
  </button>;
}

export function Tip({ content, children }: { content: ReactNode; children: ReactNode }) {
  if (!content) return <>{children}</>;
  return <RadixTooltip.Root delayDuration={250}>
    <RadixTooltip.Trigger asChild><span className="inline-flex">{children}</span></RadixTooltip.Trigger>
    <RadixTooltip.Portal><RadixTooltip.Content sideOffset={6} className="z-50 max-w-xs rounded-lg border border-line bg-surface px-3 py-2 text-xs text-ink-2 shadow-[var(--shadow-pop)]">
      {content}<RadixTooltip.Arrow className="fill-[var(--surface)]" />
    </RadixTooltip.Content></RadixTooltip.Portal>
  </RadixTooltip.Root>;
}

export function InfoTip({ content }: { content: ReactNode }) {
  return <Tip content={content}><Info size={13} className="text-muted" aria-label="More information" /></Tip>;
}

export function Dialog({ open, onOpenChange, title, description, children, wide = false }: { open: boolean; onOpenChange: (open: boolean) => void; title: string; description?: string; children: ReactNode; wide?: boolean }) {
  return <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
    <RadixDialog.Portal>
      <RadixDialog.Overlay className="fixed inset-0 z-40 bg-slate-950/40 backdrop-blur-[2px]" />
      <RadixDialog.Content className={cx('fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[calc(100vw-2rem)] -translate-x-1/2 -translate-y-1/2 overflow-y-auto rounded-2xl border border-line bg-surface p-5 shadow-[var(--shadow-pop)] sm:p-6', wide ? 'max-w-4xl' : 'max-w-lg')}>
        <div className="mb-4 flex items-start justify-between gap-4">
          <div><RadixDialog.Title className="text-lg font-semibold">{title}</RadixDialog.Title>
            {description ? <RadixDialog.Description className="hint mt-1 text-sm">{description}</RadixDialog.Description> : <RadixDialog.Description className="sr-only">{title}</RadixDialog.Description>}</div>
          <RadixDialog.Close className="btn-icon btn-ghost" aria-label="Close"><X size={18} /></RadixDialog.Close>
        </div>
        {children}
      </RadixDialog.Content>
    </RadixDialog.Portal>
  </RadixDialog.Root>;
}

export function Spinner({ label = 'Loading' }: { label?: string }) {
  return <span role="status" className="inline-flex items-center gap-2 text-sm text-muted"><Loader2 size={16} className="animate-spin" />{label}</span>;
}

export function ProgressBar({ value, label, tone = 'accent' }: { value: number; label?: string; tone?: 'accent' | 'warning' }) {
  const pct = Math.max(0, Math.min(100, value));
  return <div className="space-y-1">
    <div className="h-2 overflow-hidden rounded-full bg-surface-3" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className={cx('h-full rounded-full transition-[width] duration-500', tone === 'accent' ? 'bg-accent' : 'bg-warning')} style={{ width: `${pct}%` }} />
    </div>
    {label && <p className="hint">{label}</p>}
  </div>;
}

export function EmptyState({ icon, title, children, actions }: { icon?: ReactNode; title: string; children?: ReactNode; actions?: ReactNode }) {
  return <div className="flex flex-col items-center justify-center gap-3 px-6 py-12 text-center">
    {icon && <div className="grid h-12 w-12 place-items-center rounded-full bg-accent-soft text-accent-ink">{icon}</div>}
    <h3 className="text-base font-semibold">{title}</h3>
    {children && <div className="max-w-md text-sm text-muted">{children}</div>}
    {actions && <div className="mt-2 flex flex-wrap justify-center gap-2">{actions}</div>}
  </div>;
}

export function Notice({ tone = 'neutral', children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  const Icon = tone === 'good' ? CircleCheck : tone === 'neutral' || tone === 'active' ? Info : CircleAlert;
  return <div role={tone === 'bad' ? 'alert' : 'status'} className={cx('flex gap-2.5 rounded-xl px-3.5 py-3 text-sm', toneClass[tone])}>
    <Icon size={17} className="mt-0.5 shrink-0" /><div>{title && <p className="font-semibold">{title}</p>}<div className="opacity-95">{children}</div></div>
  </div>;
}

export function Metric({ label, value, unit, help, emphasis = false }: { label: string; value: string; unit?: string; help?: ReactNode; emphasis?: boolean }) {
  return <div className={cx('rounded-xl border border-line bg-surface-2 px-3.5 py-3', emphasis && 'border-accent/40 bg-accent-soft')}>
    <div className="flex items-center gap-1 text-xs font-medium text-muted">{label}{help && <InfoTip content={help} />}</div>
    <div className="mt-1 text-xl font-semibold tabular-nums text-ink">{value}{unit && value !== '—' && <span className="ml-1 text-sm font-medium text-muted">{unit}</span>}</div>
  </div>;
}

export { significant };

// ---- toasts -----------------------------------------------------------------------------
interface ToastItem { id: number; tone: Tone; message: string }
export const useToasts = create<{ items: ToastItem[]; push: (tone: Tone, message: string) => void; dismiss: (id: number) => void }>((set) => ({
  items: [],
  push: (tone, message) => set((state) => ({ items: [...state.items.slice(-3), { id: Date.now() + Math.random(), tone, message }] })),
  dismiss: (id) => set((state) => ({ items: state.items.filter((t) => t.id !== id) })),
}));
export const toast = {
  error: (e: unknown) => useToasts.getState().push('bad', e instanceof Error ? e.message : String(e)),
  success: (message: string) => useToasts.getState().push('good', message),
  info: (message: string) => useToasts.getState().push('active', message),
};

export function Toasts() {
  const { items, dismiss } = useToasts();
  return <div className="pointer-events-none fixed bottom-4 right-4 z-50 flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2" aria-live="polite">
    {items.map((item) => <ToastView key={item.id} item={item} onClose={() => dismiss(item.id)} />)}
  </div>;
}
function ToastView({ item, onClose }: { item: ToastItem; onClose: () => void }) {
  useEffect(() => { const t = setTimeout(onClose, item.tone === 'bad' ? 9000 : 4500); return () => clearTimeout(t); }, [item, onClose]);
  return <div className={cx('pointer-events-auto flex items-start gap-2 rounded-xl border border-line px-3.5 py-3 text-sm shadow-[var(--shadow-pop)]', 'bg-surface')}>
    <span className={cx('mt-0.5 h-2 w-2 shrink-0 rounded-full', item.tone === 'bad' ? 'bg-danger' : item.tone === 'good' ? 'bg-success' : 'bg-accent')} />
    <p className="flex-1 text-ink-2">{item.message}</p>
    <button className="text-muted hover:text-ink" onClick={onClose} aria-label="Dismiss"><X size={15} /></button>
  </div>;
}
