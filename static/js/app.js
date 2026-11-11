(function () {
  function csrf() {
    var node = document.querySelector('meta[name="csrf-token"]');
    return node ? node.getAttribute('content') : '';
  }

  function ensureKey(form) {
    if (!form) return;
    var input = form.querySelector('input[name="idempotency_key"]');
    if (input && !input.value && window.crypto && crypto.randomUUID) {
      input.value = crypto.randomUUID();
    }
  }

  document.body.addEventListener('htmx:configRequest', function (event) {
    event.detail.headers['X-CSRFToken'] = csrf();
  });

  document.body.addEventListener('htmx:beforeSwap', function (event) {
    var status = event.detail.xhr ? event.detail.xhr.status : 0;
    if (status === 400 || status === 409) {
      event.detail.shouldSwap = true;
      event.detail.isError = false;
    }
  });

  document.addEventListener('click', function (event) {
    var button = event.target.closest && event.target.closest('button, input[type="submit"]');
    if (!button || !button.form) return;
    if (button.form.matches('[data-idempotent]')) ensureKey(button.form);
    if (button.hasAttribute('data-clear-cache') && window.caches) {
      caches.keys().then(function (keys) {
        keys.forEach(function (key) { caches.delete(key); });
      });
    }
  });

  var banner = document.getElementById('save-banner');

  function showUnsaved(message) {
    if (!banner) return;
    var text = document.getElementById('save-banner-text');
    if (text) text.textContent = message || 'Not saved';
    banner.hidden = false;
  }

  document.body.addEventListener('htmx:sendError', function () {
    showUnsaved('Not saved');
  });

  document.body.addEventListener('htmx:sendAbort', function () {
    showUnsaved('Not saved');
  });

  document.body.addEventListener('htmx:responseError', function (event) {
    var status = event.detail.xhr ? event.detail.xhr.status : 0;
    if (status === 401) {
      window.location = '/login/';
      return;
    }
    var feed = event.detail.elt && event.detail.elt.closest('[data-feed-action], #feed-panel');
    showUnsaved(feed ? 'Not applied' : 'Not saved');
    refreshFeed();
  });

  document.body.addEventListener('htmx:afterRequest', function (event) {
    if (!event.detail.successful) return;
    var elt = event.detail.elt;
    var form = elt && elt.matches && elt.matches('form') ? elt : null;
    if (!form && elt && elt.closest) form = elt.closest('form');
    if (!form || !form.matches || !form.matches('form[data-idempotent]')) return;
    var input = form.querySelector('input[name="idempotency_key"]');
    if (input) input.value = '';
    if (banner) banner.hidden = true;
  });

  document.body.addEventListener('click', function (event) {
    if (!event.target.closest || !event.target.closest('[data-retry]')) return;
    var form = document.querySelector('form[data-idempotent]');
    if (!form) return;
    ensureKey(form);
    if (form.requestSubmit) form.requestSubmit();
    else form.submit();
  });

  function pollRoot() {
    return document.querySelector('[data-poll-ms]');
  }

  var pollTimer = null;

  function stopPoll() {
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function refreshPartial() {
    var root = pollRoot();
    if (!root || !window.htmx) return;
    htmx.ajax('GET', root.getAttribute('data-partial-url'), {
      target: '#timeline',
      swap: 'innerHTML',
    });
  }

  function tick() {
    var root = pollRoot();
    if (!root || document.visibilityState !== 'visible') return;
    var known = root.getAttribute('data-marker');
    fetch(root.getAttribute('data-marker-url'), {
      credentials: 'same-origin',
      headers: { 'If-None-Match': known || '' },
    }).then(function (response) {
      if (response.status === 401) {
        window.location = '/login/';
        return null;
      }
      if (response.status === 304) return null;
      return response.json();
    }).then(function (body) {
      if (!body || body.marker === known) return;
      root.setAttribute('data-marker', body.marker);
      refreshPartial();
    }).catch(function () {});
  }

  function startPoll() {
    stopPoll();
    var root = pollRoot();
    if (!root || document.visibilityState !== 'visible') return;
    var ms = Number(root.getAttribute('data-poll-ms')) || 5000;
    pollTimer = window.setInterval(tick, ms);
  }

  document.addEventListener('visibilitychange', function () {
    if (document.visibilityState === 'visible') {
      tick();
      refreshFeed();
      startPoll();
    } else {
      stopPoll();
    }
  });
  window.addEventListener('focus', function () {
    tick();
    refreshFeed();
  });
  document.body.addEventListener('refresh-timeline', tick);
  startPoll();

  var feedOffset = 0;
  var feedPayload = null;

  function renderFeed() {
    if (!feedPayload) return;
    var left = document.getElementById('feed-left');
    var right = document.getElementById('feed-right');
    if (!left || !right) return;
    var now = Date.now() + feedOffset;
    var leftSeconds = 0;
    var rightSeconds = 0;
    (feedPayload.segments || []).forEach(function (segment) {
      var start = Date.parse(segment.started_at);
      var end = segment.ended_at ? Date.parse(segment.ended_at) : now;
      var elapsed = Math.max(0, Math.round((end - start) / 1000));
      if (segment.side === 'left') leftSeconds += elapsed;
      if (segment.side === 'right') rightSeconds += elapsed;
    });
    if (!feedPayload.segments || !feedPayload.segments.length) {
      leftSeconds = feedPayload.left_seconds || 0;
      rightSeconds = feedPayload.right_seconds || 0;
    }
    left.textContent = String(leftSeconds);
    right.textContent = String(rightSeconds);
  }

  function refreshFeed() {
    var root = document.querySelector('[data-feed-state]');
    if (!root) return;
    fetch(root.getAttribute('data-state-url'), { credentials: 'same-origin' })
      .then(function (response) { return response.ok ? response.json() : null; })
      .then(function (body) {
        if (!body) return;
        feedPayload = body;
        feedOffset = Date.parse(body.server_now) - Date.now();
        renderFeed();
      })
      .catch(function () {});
  }

  window.setInterval(function () {
    if (document.visibilityState === 'visible') renderFeed();
  }, 250);
  window.setInterval(function () {
    if (document.visibilityState === 'visible' && document.querySelector('[data-feed-state]')) {
      refreshFeed();
    }
  }, 15000);
  document.body.addEventListener('htmx:afterSwap', function () {
    feedPayload = null;
    refreshFeed();
    startPoll();
  });
  refreshFeed();

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(function () {});
  }
})();
