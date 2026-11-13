const { expect } = require('@playwright/test');

const PASSWORD = 'synthetic-not-a-real-password';

async function login(page, username) {
  await page.goto('/login/');
  await page.getByLabel('Username').fill(username);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.getByRole('button', { name: 'Log in' }).click();
  await expect(page.locator('#activity')).toBeVisible();
}

module.exports = { login, PASSWORD };
