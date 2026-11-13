const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test.use({ viewport: { width: 390, height: 844 } });

test('activity home, quick-add, summary, and tabs', async ({ page }) => {
  await login(page, 'synth_parent');
  await expect(page.getByRole('heading', { name: 'Feed' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Diaper' })).toBeVisible();
  const add = page.locator('#diaper-card summary.add');
  const box = await add.boundingBox();
  expect(box.height).toBeGreaterThanOrEqual(44);

  await add.click();
  await page.getByRole('button', { name: 'Wet' }).click();
  await expect(page.locator('#save-result')).toContainText('Saved');
  await expect(page.locator('#activity').getByText('Wet', { exact: true }).first()).toBeVisible();

  await page.getByRole('button', { name: 'Summary' }).click();
  const sheet = page.locator('#summary-sheet');
  await expect(sheet.getByRole('heading', { name: 'Summary' })).toBeVisible();
  await sheet.getByRole('link', { name: 'Last 24 hours' }).click();
  await expect(sheet.getByText('Breastfeed')).toBeVisible();
  await sheet.getByRole('button', { name: 'Close' }).click();

  await page.getByRole('link', { name: 'Trends' }).click();
  await expect(page.getByRole('heading', { name: 'Trends' })).toBeVisible();
  await page.getByRole('link', { name: 'History' }).click();
  await expect(page.getByRole('heading', { name: 'Last 24 hours' })).toBeVisible();
  await page.getByRole('link', { name: 'Guides' }).click();
  await expect(page.getByRole('heading', { name: 'Guides' })).toBeVisible();
  await page.getByRole('link', { name: 'Activity' }).click();
  await expect(page.locator('#activity')).toBeVisible();

  await page.screenshot({
    path: '/opt/cursor/artifacts/activity-home.png',
  });
  await page.getByRole('button', { name: 'Summary' }).click();
  await expect(page.locator('#summary-sheet').getByRole('heading', { name: 'Summary' })).toBeVisible();
  await page.screenshot({
    path: '/opt/cursor/artifacts/summary-sheet.png',
  });
});
