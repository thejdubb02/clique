/* CLIque service worker.

Exists so the browser will offer "Install app" — a real window, no tab strip,
no URL bar. It does not cache. This panel ships many times a day; a cache
would serve yesterday's app and call it a feature.
*/

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  event.respondWith(fetch(event.request));
});
