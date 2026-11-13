const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test.use({ viewport: { width: 390, height: 844 } });

test('timeline ranges switch with HTMX and show counts', async ({ page }) => {
  await login(page, 'synth_parent');
  await page.goto('/timeline/');
  await expect(page.getByRole('heading', { name: 'Last 24 hours' })).toBeVisible();
  const dayLink = page.getByRole('link', { name: '24 hours' });
  const box = await dayLink.boundingBox();
  expect(box.height).toBeGreaterThanOrEqual(44);

  const before = Number(await page.locator('#diaper-total').innerText());

  await page.getByRole('link', { name: '7 days' }).click();
  await expect(page.getByRole('heading', { name: 'Last 7 days' })).toBeVisible();
  const week = Number(await page.locator('#diaper-total').innerText());
  expect(week).toBeGreaterThanOrEqual(before + 1);
  await expect(page.locator('#feed-total')).toBeVisible();
  await expect(page.locator('[data-kind="sleep"]')).toBeVisible();

  await page.getByRole('link', { name: '48 hours' }).click();
  await expect(page.getByRole('heading', { name: 'Last 48 hours' })).toBeVisible();
});
