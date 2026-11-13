const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test.use({ viewport: { width: 390, height: 844 } });

test('trends opens and switches ranges', async ({ page }) => {
  await login(page, 'synth_parent');
  await page.getByRole('link', { name: 'Trends' }).click();
  await expect(page.getByRole('heading', { name: 'Last 7 days' })).toBeVisible();
  await expect(page.getByRole('table').first()).toBeVisible();

  const month = page.getByRole('link', { name: '30 days' });
  const box = await month.boundingBox();
  expect(box.height).toBeGreaterThanOrEqual(44);
  await month.click();
  await expect(page.getByRole('heading', { name: 'Last 30 days' })).toBeVisible();
  await expect(page.getByRole('table').first()).toBeVisible();

  await page.getByRole('link', { name: '14 days' }).click();
  await expect(page.getByRole('heading', { name: 'Last 14 days' })).toBeVisible();
});
