self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("project-dm-shell-v2").then((cache) =>
      cache.addAll([
        "/",
        "/static/manifest.webmanifest",
        "/static/logo.png",
      ]),
    ),
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith("project-dm-shell-") && key !== "project-dm-shell-v2")
          .map((key) => caches.delete(key)),
      ),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.mode !== "navigate") {
    return;
  }
  event.respondWith(
    fetch(request).catch(async () => {
      const cache = await caches.open("project-dm-shell-v2");
      const cached = await cache.match("/");
      return cached || Response.error();
    }),
  );
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type !== "notify") return;

  event.waitUntil(
    self.registration.showNotification(data.title || "Project DM", {
      body: data.options && data.options.body ? data.options.body : "",
      icon: "/static/logo.png",
      badge: "/static/logo.png",
      data: data.options && data.options.data ? data.options.data : {},
      tag: data.options && data.options.tag ? data.options.tag : undefined,
    }),
  );
});

self.addEventListener("push", (event) => {
  const payload = event.data ? event.data.json() : {};
  const title = payload.title || "Project DM";
  const options = payload.options || {};
  event.waitUntil(
    self.registration.showNotification(title, {
      body: options.body || payload.body || "",
      icon: options.icon || "/static/logo.png",
      badge: options.badge || "/static/logo.png",
      data: options.data || {},
      tag: options.tag,
    }),
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const targetUrl = event.notification.data && event.notification.data.url
    ? event.notification.data.url
    : "/";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
      for (const client of clients) {
        if ("focus" in client && client.url === new URL(targetUrl, self.location.origin).href) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow(targetUrl);
      }
      return undefined;
    }),
  );
});
