/*
 * Postulo's service worker. It does two things and nothing else: shows a push message as a
 * notification, and opens Postulo where the notification points when it is clicked.
 *
 * No caching, no offline pages, no fetch handler. A worker that intercepts requests is a
 * second copy of the application that can go stale, and nothing here needs one. It is served
 * from the root of the site by `postulo.notifications.views.worker`, because a worker only
 * controls the path it is served from and below.
 */
"use strict";

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
