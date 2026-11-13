const { expect, test } = require('@playwright/test');
const { login } = require('./helpers');

test('a visible timeline picks up another device and pauses while hidden', async ({ browser }) => {
  const first = await browser.newContext();
  const second = await browser.newContext();
  const viewer = await first.newPage();
  const writer = await second.newPage();
  await login(viewer, 'synth_parent');
  await login(writer, 'synth_caregiver');
  const pollMs = Number(await viewer.locator('[data-poll-ms]').getAttribute('data-poll-ms'));
  test.skip(pollMs > 1500, 'TIMELINE_POLL_MS is too slow for this check');

  let hits = 0;
  await viewer.route('**/timeline/marker/**', async (route) => {
    hits += 1;
    await route.continue();
  });
  await viewer.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'hidden',
    });
    document.dispatchEvent(new Event('visibilitychange'));
  });
  const hiddenHits = hits;
  await viewer.waitForTimeout(pollMs + 800);
  expect(hits).toBe(hiddenHits);

  await viewer.evaluate(() => {
    Object.defineProperty(document, 'visibilityState', {
      configurable: true,
      get: () => 'visible',
    });
    document.dispatchEvent(new Event('visibilitychange'));
  });

  const cookies = await writer.context().cookies();
  const csrf = cookies.find((cookie) => cookie.name === 'csrftoken');
  const key = `poll-${Date.now()}`;
  const posted = await writer.request.post('/diapers/', {
    form: {
      diaper_type: 'dry',
      idempotency_key: key,
      notes: '',
    },
    headers: {
      'X-CSRFToken': csrf.value,
      Referer: 'http://127.0.0.1:8000/timeline/',
    },
  });
  expect(posted.ok()).toBeTruthy();
  await expect(viewer.locator('#activity .diaper-label', { hasText: 'Dry' }).first()).toBeVisible({
    timeout: pollMs * 4,
  });
  await first.close();
  await second.close();
});
