/* Crackmeter — service worker (installable app + offline mode)
 *
 * - The page itself: network first, so every update of the site is picked up
 *   immediately; the last copy is kept for offline use.
 * - Live data (openSenseMap, MeteoSwiss measurements and forecast): network
 *   first; the last good answer is kept and served when the network is down.
 *   openSenseMap URLs change at every request (they contain the current time),
 *   so they are stored without their query string.
 * - Versioned libraries (cdnjs, jsdelivr) and fonts: cache first.
 * Bump VERSION to force a refresh of the static files.
 */
var VERSION = 'v1';
var STATIC_CACHE = 'crackmeter-static-' + VERSION;
var DATA_CACHE = 'crackmeter-data';
var PRECACHE = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/apple-touch-icon.png'
];

self.addEventListener('install', function(event) {
  event.waitUntil(
    caches.open(STATIC_CACHE)
      .then(function(cache) { return cache.addAll(PRECACHE); })
      .then(function() { return self.skipWaiting(); })
  );
});

self.addEventListener('activate', function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(keys.map(function(k) {
        if (k.indexOf('crackmeter-static-') === 0 && k !== STATIC_CACHE) return caches.delete(k);
        return null;
      }));
    }).then(function() { return self.clients.claim(); })
  );
});

function stripSearch(url) {
  var u = new URL(url);
  u.search = '';
  return u.toString();
}

// network first; on success keep a copy under `key`, on failure answer from the cache
function networkFirst(request, cacheName, key) {
  return fetch(request).then(function(res) {
    if (res && (res.ok || res.type === 'opaque')) {
      var copy = res.clone();
      caches.open(cacheName).then(function(c) { c.put(key || request, copy); });
    }
    return res;
  }).catch(function() {
    return caches.open(cacheName).then(function(c) {
      return c.match(key || request, { ignoreSearch: !key });
    }).then(function(hit) {
      if (hit) return hit;
      throw new Error('offline and not cached');
    });
  });
}

function cacheFirst(request) {
  return caches.open(STATIC_CACHE).then(function(c) {
    return c.match(request).then(function(hit) {
      if (hit) return hit;
      return fetch(request).then(function(res) {
        if (res && (res.ok || res.type === 'opaque')) c.put(request, res.clone());
        return res;
      });
    });
  });
}

self.addEventListener('fetch', function(event) {
  var req = event.request;
  if (req.method !== 'GET') return;
  var url = new URL(req.url);

  // the app shell (page + local files)
  if (url.origin === self.location.origin) {
    if (req.mode === 'navigate') {
      event.respondWith(networkFirst(req, STATIC_CACHE, './index.html'));
      return;
    }
    if (url.pathname.indexOf('/data/') !== -1) {
      event.respondWith(networkFirst(req, DATA_CACHE, stripSearch(req.url)));
      return;
    }
    event.respondWith(networkFirst(req, STATIC_CACHE));
    return;
  }

  // live measurements and weather data
  if (url.hostname === 'api.opensensemap.org' ||
      url.hostname === 'data.geo.admin.ch' ||
      url.hostname === 'raw.githubusercontent.com') {
    event.respondWith(networkFirst(req, DATA_CACHE, stripSearch(req.url)));
    return;
  }

  // versioned libraries and fonts
  if (url.hostname === 'cdnjs.cloudflare.com' || url.hostname === 'cdn.jsdelivr.net' ||
      url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') {
    event.respondWith(cacheFirst(req));
  }
  // anything else (e.g. the Sketchfab 3D viewer) goes straight to the network
});
