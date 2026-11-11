const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test.use({ viewport: { width: 390, height: 844 } });

test('diaper button is tappable and a failed save can be retried', async ({ page }) => {
  await login(page, 'synth_parent');
  const wet = page.getByRole('button', { name: 'Wet' });
  const box = await wet.boundingBox();
  expect(box.height).toBeGreaterThanOrEqual(44);

  await wet.click();
  await expect(page.locator('#save-result')).toContainText('Saved');

  await page.evaluate(async () => {
    if (!navigator.serviceWorker) return;
    const registrations = await navigator.serviceWorker.getRegistrations();
    await Promise.all(registrations.map((registration) => registration.unregister()));
  });
  await page.route('**/diapers/', (route) => {
    if (route.request().method() === 'POST') {
      return route.fulfill({ status: 500, contentType: 'text/plain', body: 'unavailable' });
    }
    return route.continue();
  });
  await page.getByRole('button', { name: 'Dirty' }).click({ noWaitAfter: true });
  await expect(page.locator('#save-banner')).toBeVisible();
  await expect(page.locator('#save-banner-text')).toHaveText('Not saved');
  await page.unroute('**/diapers/');
  await page.locator('#save-banner').getByRole('button', { name: 'Retry' }).click({ noWaitAfter: true });
  await expect(page.locator('#save-result')).toContainText('Saved');
});
