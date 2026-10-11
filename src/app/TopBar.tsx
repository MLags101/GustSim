import * as Menu from '@radix-ui/react-dropdown-menu';
import { Link, useLocation } from 'wouter';
import { Activity, CircleHelp, Moon, Monitor, Settings, Sun, Wind } from 'lucide-react';
import { useHealth, useSimulations, isActive } from '../api/queries';
import { usePreferences, type Theme } from '../state/preferences';
import { PRESETS } from '../lib/units';
import { cx } from '../ui';
import { startTour } from '../features/onboarding/tour';

const menuItem = 'flex cursor-pointer select-none items-center gap-2 rounded-md px-2.5 py-1.5 text-sm text-ink-2 outline-none data-[highlighted]:bg-surface-3';
const menuContent = 'z-50 min-w-48 rounded-xl border border-line bg-surface p-1 shadow-[var(--shadow-pop)]';

export function TopBar() {
  const [location] = useLocation();
  const health = useHealth();
  const simulations = useSimulations();
  const { theme, setTheme, setUnitPreset } = usePreferences();
  const running = (simulations.data || []).filter((s) => isActive(s.status));
  const online = health.data?.worker.online;
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor;
  return <header className="sticky top-0 z-30 border-b border-line bg-surface/90 backdrop-blur">
    <div className="mx-auto flex h-14 max-w-[1800px] items-center gap-3 px-4">
      <Link href="/" className="flex items-center gap-2 font-semibold" aria-label="GustSim simulations">
        <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-brand to-indigo-brand text-white"><Wind size={18} /></span>
        <span className="text-lg tracking-tight">gust<span className="text-brand">sim</span></span>
      </Link>
      <nav className="ml-4 hidden items-center gap-1 sm:flex" aria-label="Main">
        <Link href="/" className={cx('btn-ghost btn-sm', location === '/' && 'bg-surface-3 text-ink')}>Simulations</Link>
        <Link href="/compare" className={cx('btn-ghost btn-sm', location.startsWith('/compare') && 'bg-surface-3 text-ink')}>Compare</Link>
      </nav>
      <div className="ml-auto flex items-center gap-1.5">
        {running.length > 0 && <Menu.Root>
          <Menu.Trigger className="btn-secondary btn-sm" aria-label={`${running.length} running`}>
            <Activity size={15} className="animate-pulse text-accent" />{running.length} running
          </Menu.Trigger>
          <Menu.Portal><Menu.Content className={menuContent} align="end" sideOffset={6}>
            {running.map((s) => <Menu.Item key={s.id} className={menuItem} onSelect={() => { window.location.hash = `/simulations/${s.id}`; }}>
              <span className="truncate">{s.name}</span><span className="ml-auto text-xs text-muted">{s.status.replace('_', ' ')}</span>
            </Menu.Item>)}
          </Menu.Content></Menu.Portal>
        </Menu.Root>}
        <span className={cx('hidden items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium md:inline-flex', online ? 'bg-success-soft text-success' : 'bg-surface-3 text-muted')}
          title={online ? `Compute worker online · OpenFOAM ${health.data?.openfoam_version}` : 'Compute worker offline: start the worker container to mesh and solve'}>
          <span className={cx('h-1.5 w-1.5 rounded-full', online ? 'bg-success' : 'bg-muted')} />{online ? 'Worker ready' : 'Worker offline'}
        </span>
        <Menu.Root>
          <Menu.Trigger className="btn-icon btn-ghost" aria-label="Theme"><ThemeIcon size={18} /></Menu.Trigger>
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
        <Menu.Root>
          <Menu.Trigger className="btn-icon btn-ghost" aria-label="Help"><CircleHelp size={18} /></Menu.Trigger>
          <Menu.Portal><Menu.Content className={menuContent} align="end" sideOffset={6}>
            <Menu.Item className={menuItem} onSelect={() => startTour(true)}>Take the tour</Menu.Item>
            <Menu.Item className={menuItem} onSelect={() => { window.location.hash = '/settings'; }}><Settings size={15} />Settings &amp; about</Menu.Item>
          </Menu.Content></Menu.Portal>
        </Menu.Root>
      </div>
    </div>
  </header>;
}
