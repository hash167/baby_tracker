const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test.use({ viewport: { width: 390, height: 844 } });

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

test('trends range buttons and stats meet contrast', async ({ page }) => {
  await login(page, 'synth_parent');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/trends/');
  await expect(page.locator('.stat-label', { hasText: 'Diapers/day' })).toBeVisible();
  const selected = page.getByRole('link', { name: '7 days' });
  const unselected = page.getByRole('link', { name: '14 days' });
  const label = page.locator('.stat-label', { hasText: 'Diapers/day' });
  for (const item of [selected, unselected, label]) {
    const pair = await colors(item);
    expect(contrastRatio(pair.foreground, pair.background)).toBeGreaterThanOrEqual(4.5);
  }
  await page.screenshot({ path: '/opt/cursor/artifacts/trends-dark.png' });
});

test('trends opens and switches ranges', async ({ page }) => {
  await login(page, 'synth_parent');
  await page.getByRole('link', { name: 'Trends' }).click();
  await expect(page.getByRole('heading', { name: 'Last 7 days' })).toBeVisible();
  await expect(page.getByRole('table').first()).toBeVisible();

  const month = page.getByRole('link', { name: '30 days' });
  const box = await month.boundingBox();
  expect(box.height).toBeGreaterThanOrEqual(44);
  await month.click();
  await expect(page.getByRole('heading', { name: 'Last 30 days' })).toBeVisible();
  await expect(page.getByRole('table').first()).toBeVisible();

  await page.getByRole('link', { name: '14 days' }).click();
  await expect(page.getByRole('heading', { name: 'Last 14 days' })).toBeVisible();
});
