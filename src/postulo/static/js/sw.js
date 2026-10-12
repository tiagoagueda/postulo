/*
 * Postulo's service worker. It does three things and nothing else: shows a push message as a
 * notification, opens Postulo where the notification points when it is clicked, and shows
 * one page of its own when a page cannot be loaded at all (#895).
 *
 * That page is the only thing it keeps. A worker that answers requests from a cache is a
 * second copy of the application that can go stale, and a copy of somebody's applications
 * left on a device is exactly what signing out is meant to end -- so a page load goes to
 * the network every time, and only when the network gives nothing at all (no connection,
 * no server) is the kept "cannot reach its server" page shown in its place. Nothing else is
 * read from or written to a cache: no page, no stylesheet, no data.
 *
 * It is served from the root of the site by `postulo.notifications.views.worker`, because a
 * worker only controls the path it is served from and below.
 */
"use strict";

// The kept page, under a name that says which version of this rule kept it: a worker that
// changes how it keeps the page changes the name, and the old cache goes on activation.
var OFFLINE_CACHE = "postulo-offline-1";
var OFFLINE_PAGE = "/offline/";

function keepOfflinePage() {
  return fetch(OFFLINE_PAGE, { credentials: "same-origin", cache: "no-store" }).then(function (response) {
    if (!response.ok) {
      return null;
    }
    return caches.open(OFFLINE_CACHE).then(function (cache) {
      return cache.put(OFFLINE_PAGE, response);
    });
  });
}

self.addEventListener("install", function (event) {
  // A page that cannot be kept now is kept the next time a tab asks (the message below);
  // installing is not held up for it, or a worker could never install while offline.
  event.waitUntil(keepOfflinePage().catch(function () {}).then(function () {
    return self.skipWaiting();
  }));
});

self.addEventListener("activate", function (event) {
  event.waitUntil(
    caches.keys().then(function (names) {
      return Promise.all(
        names.map(function (name) {
          return name.indexOf("postulo-offline-") === 0 && name !== OFFLINE_CACHE ? caches.delete(name) : null;
        })
      );
    }).then(function () {
      // The network starts while the worker is still waking, so passing a page load
      // through it costs nothing that a visitor waits for.
      if (self.registration.navigationPreload) {
        return self.registration.navigationPreload.enable();
      }
      return null;
    }).then(function () {
      return self.clients.claim();
    })
  );
});

// A page load, and nothing else: no script, picture or request a page makes, and no form
// sent, so a POST that fails is the browser's to report and is never answered with a page
// that would pretend it had gone somewhere. Another site's address is not ours to answer.
self.addEventListener("fetch", function (event) {
  var request = event.request;
  if (request.mode !== "navigate" || request.method !== "GET") {
    return;
  }
  if (new URL(request.url).origin !== self.location.origin) {
    return;
  }
  event.respondWith(
    Promise.resolve(event.preloadResponse).then(function (preloaded) {
      return preloaded || fetch(request);
    }).catch(function () {
      return caches.match(OFFLINE_PAGE, { cacheName: OFFLINE_CACHE }).then(function (kept) {
        return kept || Response.error();
      });
    })
  );
});

// A tab says which language it is in, and the kept page follows it: chosen once, at
// install, it would go on speaking the language the account had that day.
self.addEventListener("message", function (event) {
  var data = event.data || {};
  if (data.type !== "offline-page" || event.origin !== self.location.origin) {
    return;
  }
  event.waitUntil(
    caches.match(OFFLINE_PAGE, { cacheName: OFFLINE_CACHE }).then(function (kept) {
      var language = kept ? (kept.headers.get("Content-Language") || "").toLowerCase() : "";
      if (kept && language === String(data.language || "").toLowerCase()) {
        return null;
      }
      return keepOfflinePage();
    }).catch(function () {})
  );
});

self.addEventListener("push", function (event) {
  var data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (error) {
      data = { title: event.data.text() };
    }
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "Postulo", {
      body: data.body || "",
      icon: data.icon || undefined,
      tag: data.tag || undefined,
      data: { url: data.url || "/" },
    })
  );
});

// Only ever somewhere on this site. The address travels inside an encrypted message from this
// instance, so it should always be local already -- but a notification that could open an
// arbitrary page would be a link nobody could see before following it.
function sameSite(address) {
  try {
    var target = new URL(address || "/", self.location.origin);
    return target.origin === self.location.origin ? target : new URL("/", self.location.origin);
  } catch (error) {
    return new URL("/", self.location.origin);
  }
}

self.addEventListener("notificationclick", function (event) {
  event.notification.close();
  var target = sameSite(event.notification.data && event.notification.data.url);
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (windows) {
      for (var i = 0; i < windows.length; i++) {
        if (windows[i].url === target.href && "focus" in windows[i]) {
          return windows[i].focus();
        }
      }
      return self.clients.openWindow(target.href);
    })
  );
});
