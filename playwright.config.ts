import { defineConfig, devices } from '@playwright/test';

// Runs against a GustSim API serving the built UI. For a local run:
//   GUSTSIM_DATA=<empty scratch dir> python scripts/ui_fixture_data.py
//   GUSTSIM_DATA=<same dir> PYTHONPATH=backend python -m uvicorn gustsim.api:app --port 8765
//   E2E_BASE_URL=http://127.0.0.1:8765 pnpm exec playwright test
export default defineConfig({
  testDir: 'e2e',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:8765',
    viewport: { width: 1440, height: 900 },
    trace: 'retain-on-failure',
    // Software WebGL so vtk.js renders headless.
    launchOptions: { args: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist'] },
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
