const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

function contrastRatio(foreground, background) {
  const channel = (value) => {
    const scaled = value / 255;
    return scaled <= 0.03928 ? scaled / 12.92 : ((scaled + 0.055) / 1.055) ** 2.4;
  };
  const lum = (rgb) => 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
  const lighter = Math.max(lum(foreground), lum(background));
  const darker = Math.min(lum(foreground), lum(background));
  return (lighter + 0.05) / (darker + 0.05);
}

async function colors(locator) {
  return locator.evaluate((el) => {
    const parse = (value) => {
      const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([\d.]+))?\)/);
      if (!match) return null;
      return {
        rgb: [Number(match[1]), Number(match[2]), Number(match[3])],
        alpha: match[4] === undefined ? 1 : Number(match[4]),
      };
    };
    let node = el;
    let background = null;
    while (node) {
      const parsed = parse(getComputedStyle(node).backgroundColor);
      if (parsed && parsed.alpha > 0.9) {
        background = parsed.rgb;
        break;
      }
      node = node.parentElement;
    }
    return {
      foreground: parse(getComputedStyle(el).color).rgb,
      background,
    };
  });
}

test.use({ viewport: { width: 390, height: 844 } });

test('activity home, quick-add, summary, and tabs', async ({ page }) => {
  await login(page, 'synth_parent');
  await expect(page.getByRole('heading', { name: 'Feed' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Diaper' })).toBeVisible();
  const add = page.locator('#diaper-card summary.add');
  const box = await add.boundingBox();
  expect(box.height).toBeGreaterThanOrEqual(44);

  await add.click();
  const when = page.locator('#diaper-card input[name="occurred_at"]');
  const notes = page.locator('#diaper-card textarea[name="notes"]');
  for (const field of [when, notes]) {
    const pair = await colors(field);
    expect(contrastRatio(pair.foreground, pair.background)).toBeGreaterThanOrEqual(4.5);
  }
  await page.getByRole('radio', { name: 'Wet' }).check();
  await page.getByRole('button', { name: 'Save diaper' }).click();
  await expect(page.locator('#log-toast')).toContainText('Saved Wet diaper');
  await expect(page.locator('#activity .diaper-label', { hasText: /^Wet$/ }).first()).toBeVisible();

  await page.getByRole('button', { name: 'Summary' }).click();
  const sheet = page.locator('#summary-sheet');
  await expect(sheet.getByRole('heading', { name: 'Summary' })).toBeVisible();
  await sheet.getByRole('link', { name: 'Last 24 hours' }).click();
  await expect(sheet.getByText('Breastfeed')).toBeVisible();
  await sheet.getByRole('button', { name: 'Close' }).click();

  await page.getByRole('link', { name: 'Trends' }).click();
  await expect(page.getByRole('heading', { name: 'Trends' })).toBeVisible();
  await page.getByRole('link', { name: 'History' }).click();
  await expect(page.getByRole('heading', { name: 'Last 24 hours' })).toBeVisible();
  await page.getByRole('link', { name: 'Guides' }).click();
  await expect(page.getByRole('heading', { name: 'Guides' })).toBeVisible();
  await page.getByRole('link', { name: 'Activity' }).click();
  await expect(page.locator('#activity')).toBeVisible();

  await page.screenshot({
    path: '/opt/cursor/artifacts/activity-home.png',
  });
  await page.getByRole('button', { name: 'Summary' }).click();
  await expect(page.locator('#summary-sheet').getByRole('heading', { name: 'Summary' })).toBeVisible();
  await page.screenshot({
    path: '/opt/cursor/artifacts/summary-sheet.png',
  });
});
