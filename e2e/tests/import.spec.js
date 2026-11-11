const { expect, test } = require('@playwright/test');
const path = require('path');
const { login } = require('./helpers');

test('parent previews and commits a synthetic import', async ({ page }) => {
  await login(page, 'synth_admin');
  await page.goto('/import/');
  await page.setInputFiles(
    'input[name="export"]',
    path.join(__dirname, '../../tests/fixtures/nara/synthetic_basic.csv'),
  );
  await page.getByRole('button', { name: 'Stage preview' }).click();
  await expect(page.getByRole('heading', { name: 'Preview' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Diaper (2)' })).toBeVisible();
  await expect(page.getByText('unknown_enum')).toBeVisible();
  await page.getByRole('button', { name: 'Commit' }).click();
  await page.goto('/timeline/2026-06-15/');
  await expect(page.getByText('wet', { exact: false }).first()).toBeVisible();
});

test('caregiver cannot open import', async ({ page }) => {
  await login(page, 'synth_caregiver');
  const response = await page.goto('/import/');
  expect(response.status()).toBe(403);
});
