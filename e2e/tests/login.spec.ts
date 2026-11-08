import { expect, test } from '@playwright/test';

// Product login arrives with invite-only accounts. Until then the smoke
// test loads Django's admin login, which is the login page that exists.
test('login page loads', async ({ page }) => {
  const response = await page.goto('/admin/login/');

  expect(response).not.toBeNull();
  expect(response!.ok()).toBeTruthy();
  await expect(page).toHaveTitle(/Log in/);
  await expect(page.locator('#login-form input[name="username"]')).toBeVisible();
  await expect(page.locator('#login-form input[name="password"]')).toBeVisible();
  await expect(page.locator('#login-form input[type="submit"]')).toHaveValue('Log in');
});
