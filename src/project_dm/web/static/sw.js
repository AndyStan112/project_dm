self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type !== "notify") return;

  event.waitUntil(
    self.registration.showNotification(data.title || "Project DM", {
      body: data.options && data.options.body ? data.options.body : "",
      icon: "/icon.svg",
      badge: "/icon.svg",
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
      icon: options.icon || "/icon.svg",
      badge: options.badge || "/icon.svg",
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
