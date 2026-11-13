const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test('login page loads and logout returns to it', async ({ page }) => {
  const response = await page.goto('/login/');
  expect(response.ok()).toBeTruthy();
  await expect(page).toHaveTitle(/Log in/);
  await expect(page.getByLabel('Username')).toBeVisible();
  await login(page, 'synth_parent');
  await page.goto('/sessions/');
  await page.getByRole('button', { name: 'Log out' }).click();
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible();
  await page.goto('/timeline/');
  await expect(page.getByRole('heading', { name: 'Log in' })).toBeVisible();
});

test('signup is closed', async ({ page }) => {
  const response = await page.goto('/signup/');
  expect(response.status()).toBe(404);
});

