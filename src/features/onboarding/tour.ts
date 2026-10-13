import { driver, type DriveStep } from 'driver.js';
import 'driver.js/dist/driver.css';
import { usePreferences } from '../../state/preferences';

const STEPS: DriveStep[] = [
  { element: '[data-tour="new-simulation"]', popover: { title: 'Start a simulation', description: 'Import a STEP, IGES, STL, OBJ or glTF model, or try a sample. Nothing leaves this computer.' } },
  { element: '[data-tour="setup-geometry-diagnostics"]', popover: { title: 'Check and orient the model', description: 'GustSim checks the surface, lets you prepare or wrap it, and choose the parts to measure.' } },
  { element: '[data-tour="setup-incident-flow"]', popover: { title: 'Set the flow', description: 'Speed, direction of travel, rotor speed or pipe inlet. Arrows in the 3D view show the incoming flow.' } },
  { element: '[data-tour="setup-mesh-resolution"]', popover: { title: 'Pick an accuracy', description: 'Basic, Regular or Advanced, with cell count, memory and time estimates for this computer.' } },
  { element: '[data-tour="launch"]', popover: { title: 'Review and launch', description: 'Confirm what GustSim inferred; the simulation meshes, solves until forces converge and builds the report.' } },
  { element: '[data-tour="results-tabs"]', popover: { title: 'Explore results', description: 'Pressure and friction maps, loss clouds, vortices, streamlines, sections, forces by part and rule-based insights.' } },
  { element: '[data-tour="camera-presets"]', popover: { title: 'Views and screenshots', description: 'Camera presets follow the flow direction; the camera button saves an annotated PNG.' } },
];

/** Steps whose target is on the current page are shown; the rest are skipped. */
export function startTour(force = false) {
  const { tourCompleted, setTourCompleted } = usePreferences.getState();
  if (tourCompleted && !force) return;
  // Never cover an open dialog (e.g. New simulation) or start on a page the user is leaving.
  if (!force && document.querySelector('[role="dialog"]')) return;
  const steps = STEPS.filter((s) => !s.element || document.querySelector(s.element as string));
  if (!steps.length) return;
  const reduce = typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const tour = driver({ showProgress: true, animate: !reduce, steps, onDestroyed: () => setTourCompleted(true), nextBtnText: 'Next', prevBtnText: 'Back', doneBtnText: 'Done' });
  // The tour belongs to the page it started on: leaving the page ends it, so its overlay never
  // blocks the next page.
  const page = window.location.hash;
  const leave = () => { if (tour.isActive()) tour.destroy(); };
  window.addEventListener('hashchange', leave, { once: true });
  tour.drive();
  if (window.location.hash !== page) leave();
}
