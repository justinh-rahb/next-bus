// Caches the app shell so the home page (with saved stops) opens offline.
// Departure times are never served from cache: stale times are worse than none.
const CACHE = "nextbus-__VERSION__";
const SHELL = [
  "/",
  "/offline",
  "/static/styles.css?v=__VERSION__",
  "/static/app.js?v=__VERSION__",
  "/static/icons/next-bus.svg",
  "/static/icons/icon-192x192.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  const url = new URL(request.url);
  if (request.method !== "GET" || url.origin !== location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(
      fetch(request).catch(async () => {
        const cache = await caches.open(CACHE);
        return (url.pathname === "/" && (await cache.match("/"))) || cache.match("/offline");
      }),
    );
    return;
  }

  if (url.pathname.startsWith("/static/")) {
    event.respondWith(caches.match(request).then((hit) => hit || fetch(request)));
  }
});
