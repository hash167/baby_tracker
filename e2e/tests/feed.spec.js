const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test('timer survives reload and a clock jump', async ({ page, context }) => {
  await page.clock.install();
  await login(page, 'synth_parent');
  await page.goto('/feed/');
  const existing = page.getByRole('button', { name: 'Finish' });
  if (await existing.isVisible()) {
    await existing.click();
    await expect(page.getByRole('button', { name: 'Start left' })).toBeVisible();
  }
  await page.getByRole('button', { name: 'Start left' }).click();
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();
  await page.reload();
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();

  await page.clock.fastForward(5000);
  await expect(page.locator('#feed-left')).not.toHaveText('0', { timeout: 5000 });

  const other = await context.newPage();
  await other.goto('/feed/');
  await expect(other.locator('#feed-status')).toContainText('active');
  await other.close();

  await page.getByRole('button', { name: 'Finish' }).click();
  await expect(page.locator('#feed-status')).toContainText('finished');
  await expect(page.getByRole('button', { name: 'Start left' })).toBeVisible();
});
