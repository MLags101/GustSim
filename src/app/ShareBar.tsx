import * as Menu from '@radix-ui/react-dropdown-menu';
import { Moon, Monitor, Sun, Wind } from 'lucide-react';
import { usePreferences, type Theme } from '../state/preferences';
import { PRESETS } from '../lib/units';
import { share } from '../share/bundle';

const menuItem = 'flex cursor-pointer select-none items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-ink-2 outline-none data-[highlighted]:bg-surface-3';
const menuContent = 'z-50 min-w-48 rounded-xl border border-line bg-surface p-1 shadow-[var(--shadow-pop)]';

/** Header of the offline share bundle: what this file is, when it was exported, display options. */
export function ShareBar() {
  const { theme, setTheme, setUnitPreset } = usePreferences();
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor;
  const exported = share ? new Date(share.created*1000).toLocaleString() : '';
  return <header className="sticky top-0 z-30 border-b border-line bg-surface/90 backdrop-blur">
    <div className="mx-auto flex h-14 max-w-[1800px] items-center gap-3 px-4">
      <span className="flex items-center gap-2 font-semibold">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-brand to-indigo-brand text-white"><Wind size={18} /></span>
        <span className="text-lg tracking-tight">gust<span className="text-brand">sim</span></span>
      </span>
      <span className="chip" title="Everything in this file is embedded; it needs no network or GustSim installation.">Shared report · read-only · works offline</span>
      <span className="hidden text-xs text-muted md:inline">Exported {exported}</span>
      <div className="ml-auto">
        <Menu.Root>
          <Menu.Trigger className="btn-icon btn-ghost" aria-label="Display options"><ThemeIcon size={18} /></Menu.Trigger>
          <Menu.Portal><Menu.Content className={menuContent} align="end" sideOffset={6}>
            <Menu.Label className="eyebrow px-2.5 py-1">Theme</Menu.Label>
            {(['light', 'dark', 'system'] as Theme[]).map((t) => <Menu.Item key={t} className={menuItem} onSelect={() => setTheme(t)}>
              {t === 'light' ? <Sun size={15} /> : t === 'dark' ? <Moon size={15} /> : <Monitor size={15} />}
              <span className="capitalize">{t}</span>{theme === t && <span className="ml-auto text-accent">●</span>}
            </Menu.Item>)}
            <Menu.Separator className="my-1 h-px bg-line" />
            <Menu.Label className="eyebrow px-2.5 py-1">Units</Menu.Label>
            {Object.keys(PRESETS).map((p) => <Menu.Item key={p} className={menuItem} onSelect={() => setUnitPreset(p as keyof typeof PRESETS)}>{p}</Menu.Item>)}
          </Menu.Content></Menu.Portal>
        </Menu.Root>
      </div>
    </div>
    {share && share.omitted.length > 0 && <div className="mx-auto max-w-[1800px] px-4 pb-2 text-xs text-muted">
      {share.omitted.length} result file(s) were left out to keep this report small; those layers show as unavailable.</div>}
  </header>;
}
