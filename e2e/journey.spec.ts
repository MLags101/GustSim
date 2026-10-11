import { expect, test, type Page } from '@playwright/test';

async function canvasHasPixels(page: Page) {
  // A rendered model compresses far worse than an empty background, so the PNG size of the
  // canvas is a robust non-blank check (WebGL buffers cannot be read back after compositing).
  const canvas = page.locator('canvas').first();
  if (!(await canvas.count())) return false;
  const image = await canvas.screenshot();
  return image.length > 12000;
}

test('dashboard lists simulations with evidence and filters', async ({ page }) => {
  await page.goto('/#/');
  await expect(page.getByRole('heading', { name: 'Simulations' })).toBeVisible();
  await expect(page.getByText('Exp: Pending').first()).toBeVisible();
  await page.getByLabel('Search simulations').fill('25 m/s');
  await expect(page.getByRole('row')).toHaveCount(2);
});

test('results: flow, forces, insights, downloads', async ({ page, request }) => {
  const simulations = await (await request.get('/api/simulations')).json();
  const done = simulations.find((s: any) => s.status === 'completed');
  test.skip(!done, 'needs a completed simulation (scripts/ui_fixture_data.py)');
  await page.goto(`/#/simulations/${done.id}/flow`);
  await expect(page.getByText('Experimental validation').first()).toBeVisible();
  await expect(page.getByText('Pressure (Cp)').last()).toBeVisible();
  await expect.poll(() => canvasHasPixels(page), { timeout: 15000 }).toBe(true);
  await page.getByRole('tab', { name: 'Forces' }).click();
  await expect(page.getByText('Drag coefficient in context')).toBeVisible();
  await page.getByRole('tab', { name: /Insights/ }).click();
  await expect(page.getByText('Flow insights')).toBeVisible();
  await page.getByRole('tab', { name: 'Report & downloads' }).click();
  await expect(page.getByText('PDF report')).toBeVisible();
});

test('shared report opens offline from a single file', async ({ browser, request }, testInfo) => {
  const simulations = await (await request.get('/api/simulations')).json();
  const done = simulations.find((s: any) => s.status === 'completed' && s.results_run_id);
  test.skip(!done, 'needs a completed simulation (scripts/ui_fixture_data.py)');
  const exported = await request.get(`/api/runs/${done.results_run_id}/export/share`);
  expect(exported.status()).toBe(200);
  const file = testInfo.outputPath('shared-report.html');
  const { writeFile } = await import('node:fs/promises');
  const { pathToFileURL } = await import('node:url');
  await writeFile(file, await exported.body());
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const requests: string[] = [];
  await context.route('**/*', (route) => {
    const url = route.request().url();
    if (/^(file|data|blob):/.test(url)) return route.continue();
    requests.push(url);
    return route.abort();
  });
  const page = await context.newPage();
  await page.goto(pathToFileURL(file).href);
  await expect(page.getByText('Shared report · read-only · works offline')).toBeVisible();
  await expect(page.getByText('Experimental validation').first()).toBeVisible();
  await expect(page.getByRole('tab', { name: 'Report & downloads' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /Duplicate/ })).toHaveCount(0);
  await expect.poll(() => canvasHasPixels(page), { timeout: 20000 }).toBe(true);
  await page.getByRole('tab', { name: 'Forces' }).click();
  await expect(page.getByText('Drag coefficient in context')).toBeVisible();
  expect(requests, 'the shared report must not touch the network').toEqual([]);
  await context.close();
});

test('new simulation from a sample opens the setup with checks', async ({ page }) => {
  await page.goto('/#/');
  await page.getByRole('button', { name: 'New simulation' }).first().click();
  await page.getByRole('radio', { name: /Samples/ }).click();
  await page.getByRole('button', { name: /Sphere/ }).click();
  await expect(page.getByRole('heading', { name: 'Model' })).toBeVisible({ timeout: 20000 });
  await expect(page.getByText('Closed surface', { exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Accuracy' })).toBeVisible();
  // Launch stays disabled until the inferred setup is confirmed.
  await expect(page.getByRole('button', { name: 'Launch simulation' })).toBeDisabled({ timeout: 20000 });
  await page.getByText('I checked the model size').click();
  await expect.poll(() => canvasHasPixels(page), { timeout: 15000 }).toBe(true);
  const acknowledge = page.getByText(/I read the items marked/);
  if (await acknowledge.count()) await acknowledge.click();
  await expect(page.getByRole('button', { name: 'Launch simulation' })).toBeEnabled({ timeout: 20000 });
  await page.getByRole('button', { name: 'Launch simulation' }).click();
  await expect(page).toHaveURL(/#\/simulations\//, { timeout: 20000 });
  await expect(page.getByText('Queued').first()).toBeVisible();
  await expect(page.getByText('Waiting for the compute worker')).toBeVisible();
});

test('setup elements: ground, ride height and a fitted radiator', async ({ page }) => {
  await page.goto('/#/');
  await page.getByRole('button', { name: 'New simulation' }).first().click();
  await page.getByRole('radio', { name: /Samples/ }).click();
  await page.getByRole('button', { name: /Sphere/ }).click();
  await expect(page.getByRole('heading', { name: 'Model' })).toBeVisible({ timeout: 20000 });
  await expect(page.getByRole('spinbutton', { name: /Ride height/ })).toHaveCount(0);
  await page.getByRole('radio', { name: 'Moving ground' }).click();
  await expect(page.getByRole('spinbutton', { name: /Ride height/ })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Detect wheels' })).toBeEnabled();
  await page.getByRole('region', { name: 'Radiators (porous zones)' }).getByRole('button', { name: 'Add' }).click();
  await expect(page.getByText(/Fitted Darcy–Forchheimer: d = /)).toBeVisible({ timeout: 10000 });
  await expect(page.getByText('Building the setup')).toHaveCount(0, { timeout: 20000 });
  await expect(page.getByText(/ M cells/).first()).toBeVisible({ timeout: 20000 });
  if (process.env.E2E_SHOTS) await page.screenshot({ path: `${process.env.E2E_SHOTS}/setup-elements.png`, fullPage: true });
});

test('theme preference persists', async ({ page }) => {
  await page.goto('/#/');
  await page.getByRole('button', { name: 'Theme' }).click();
  await page.getByRole('menuitem', { name: /dark/i }).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});
