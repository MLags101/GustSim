import { lazy, Suspense, useEffect } from 'react';
import { QueryClientProvider } from '@tanstack/react-query';
import * as RadixTooltip from '@radix-ui/react-tooltip';
import { Router, Route, Switch, Redirect } from 'wouter';
import { useHashLocation } from 'wouter/use-hash-location';
import { queryClient, useHealth } from '../api/queries';
import { resolvedTheme, usePreferences } from '../state/preferences';
import { Spinner, Toasts, Notice } from '../ui';
import { TopBar } from './TopBar';
import { BrowserTools } from '../agent/BrowserTools';
import { DashboardPage } from '../features/dashboard/DashboardPage';
import { isShare, share } from '../share/bundle';
import { ShareBar } from './ShareBar';

const SetupPage = lazy(() => import('../features/setup/SetupPage'));
const SimulationPage = lazy(() => import('../features/simulation/SimulationPage'));
const ComparePage = lazy(() => import('../features/compare/ComparePage'));
const SettingsPage = lazy(() => import('../features/settings/SettingsPage'));
const BatchPage = lazy(() => import('../features/batches/BatchPage'));

function ThemeSync() {
  const theme = usePreferences((s) => s.theme);
  useEffect(() => {
    const apply = () => { document.documentElement.dataset.theme = resolvedTheme(theme); };
    apply();
    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    media?.addEventListener?.('change', apply);
    return () => media?.removeEventListener?.('change', apply);
  }, [theme]);
  return null;
}

function OfflineBanner() {
  const health = useHealth();
  if (!health.isError) return null;
  return <div className="px-4 pt-3"><Notice tone="bad">Cannot reach the GustSim API. Your work is kept; reconnecting automatically.</Notice></div>;
}

/** The offline share bundle: one simulation's results, read-only, no API. */
function SharedReport() {
  const home = `/simulations/${share!.simulation_id}/flow`;
  return <div className="flex min-h-full flex-col">
    <ShareBar />
    <main className="flex min-h-0 flex-1 flex-col">
      <Suspense fallback={<div className="grid flex-1 place-items-center"><Spinner label="Loading" /></div>}>
        <Switch>
          <Route path="/simulations/:id/:tab?">{(params) => <SimulationPage id={params.id} tab={params.tab} />}</Route>
          <Route><Redirect to={home} /></Route>
        </Switch>
      </Suspense>
    </main>
  </div>;
}

export function App() {
  if (isShare) return <QueryClientProvider client={queryClient}>
    <RadixTooltip.Provider>
      <ThemeSync />
      <Router hook={useHashLocation}><SharedReport /></Router>
      <Toasts />
    </RadixTooltip.Provider>
  </QueryClientProvider>;
  return <QueryClientProvider client={queryClient}>
    <RadixTooltip.Provider>
      <ThemeSync />
      <Router hook={useHashLocation}>
        <div className="flex min-h-full flex-col">
          <TopBar />
          <OfflineBanner />
          <main className="flex min-h-0 flex-1 flex-col">
            <Suspense fallback={<div className="grid flex-1 place-items-center"><Spinner label="Loading" /></div>}>
              <Switch>
                <Route path="/" component={DashboardPage} />
                <Route path="/setup/:projectId">{(params) => <SetupPage projectId={params.projectId} />}</Route>
                <Route path="/simulations/:id/:tab?">{(params) => <SimulationPage id={params.id} tab={params.tab} />}</Route>
                <Route path="/batches/:id">{(params) => <BatchPage id={params.id} />}</Route>
                <Route path="/compare" component={ComparePage} />
                <Route path="/settings" component={SettingsPage} />
                <Route><Redirect to="/" /></Route>
              </Switch>
            </Suspense>
          </main>
        </div>
        <BrowserTools />
      </Router>
      <Toasts />
    </RadixTooltip.Provider>
  </QueryClientProvider>;
}
