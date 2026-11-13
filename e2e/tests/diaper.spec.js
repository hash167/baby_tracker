const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test.use({ viewport: { width: 390, height: 844 } });

async function openDiaper(page) {
  await page.locator('#diaper-card summary.add').click();
}

test('diaper save confirms, can be undone, and a failed save can be retried', async ({ page }) => {
  await login(page, 'synth_parent');
  await page.goto('/timeline/');
  await expect(page.getByRole('heading', { name: 'History' })).toBeVisible();
  await expect(page.locator('form[action*="diapers"]')).toHaveCount(0);
  await expect(page.locator('textarea[name="notes"]')).toHaveCount(0);
  await expect(page.locator('input[name="occurred_at"]')).toHaveCount(0);

  await page.goto('/');
  await openDiaper(page);
  const when = page.locator('input[name="occurred_at"]');
  const notes = page.locator('textarea[name="notes"]');
  await expect(when).toBeVisible();
  await expect(notes).toBeVisible();

  await page.getByRole('radio', { name: 'Wet' }).check();
  await page.getByRole('button', { name: 'Save diaper' }).click();
  const toast = page.locator('#log-toast');
  await expect(toast).toContainText('Saved Wet diaper');
  await expect(toast).toContainText('Today');
  await expect(page.locator('#activity .diaper-label', { hasText: /^Wet$/ }).first()).toBeVisible();

  await toast.getByRole('button', { name: 'Undo' }).click();
  await expect(toast).toContainText('Removed');
  await page.waitForLoadState('networkidle');

  await openDiaper(page);
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
  await page.getByRole('radio', { name: 'Dirty' }).check();
  await page.getByRole('button', { name: 'Save diaper' }).click({ noWaitAfter: true });
  await expect(page.locator('#save-banner')).toBeVisible();
  await expect(page.locator('#save-banner-text')).toHaveText('Not saved');
  await page.unroute('**/diapers/');
  await page.locator('#save-banner').getByRole('button', { name: 'Retry' }).click({ noWaitAfter: true });
  await expect(page.locator('#log-toast')).toContainText('Saved Dirty diaper');
});

test('wet and dirty diapers show their labels and dates', async ({ page }) => {
  await login(page, 'synth_parent');
  await openDiaper(page);
  await page.getByRole('radio', { name: 'Wet' }).check();
  await page.getByRole('button', { name: 'Save diaper' }).click();
  await expect(page.locator('#log-toast')).toContainText('Saved Wet diaper');
  const activity = page.locator('#activity');
  await expect(activity.locator('.diaper-label', { hasText: /^Wet$/ }).first()).toBeVisible();
  await page.waitForLoadState('networkidle');

  await openDiaper(page);
  await page.getByRole('radio', { name: 'Dirty' }).check();
  await page.getByRole('button', { name: 'Save diaper' }).click();
  await expect(activity.locator('.diaper-label', { hasText: /^Dirty$/ }).first()).toBeVisible();

  await page.goto('/timeline/?range=48h');
  await expect(page.locator('.event-time', { hasText: 'Today' }).first()).toBeVisible();
  await expect(page.locator('.day-header', { hasText: 'Today,' }).first()).toBeVisible();
});
