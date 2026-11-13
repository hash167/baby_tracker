const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test('admin invite creates one account and can sign a caregiver out', async ({ browser }) => {
  const adminContext = await browser.newContext();
  const admin = await adminContext.newPage();
  await login(admin, 'synth_admin');
  await admin.goto('/portal/');
  const email = `synth-doula-${Date.now()}@example.com`;
  const invite = admin.locator('form', { has: admin.locator('input[value="invite"]') });
  await invite.getByLabel('Email').fill(email);
  await invite.getByLabel('Role').selectOption('caregiver');
  await invite.getByRole('button', { name: 'Create invite link' }).click();
  const link = await admin.locator('#invite-link a').getAttribute('href');
  expect(link).toContain('/invite/');

  const guestContext = await browser.newContext();
  const guest = await guestContext.newPage();
  await guest.goto(link);
  await guest.getByLabel('Password', { exact: true }).fill('synthetic-pass-word-1');
  await guest.getByLabel('Confirm password').fill('synthetic-pass-word-1');
  await guest.getByRole('button', { name: 'Create account' }).click();
  await expect(guest.getByRole('heading', { name: 'Timeline' })).toBeVisible();
  await guest.goto(link);
  await expect(guest.getByText('already used')).toBeVisible();

  const caregiverContext = await browser.newContext();
  const caregiver = await caregiverContext.newPage();
  await login(caregiver, 'synth_caregiver');
  await expect(caregiver.locator('#activity')).toBeVisible();
  const row = admin.locator('li', { hasText: 'synth_caregiver' });
  await row.getByRole('button', { name: 'Sign out everywhere' }).click();
  await caregiver.goto('/timeline/');
  await expect(caregiver.getByRole('heading', { name: 'Log in' })).toBeVisible();

  await adminContext.close();
  await guestContext.close();
  await caregiverContext.close();
});
