/* CLIque service worker.

Exists so the browser will offer "Install app": a real window, no tab strip,
no URL bar. It does not cache the app. This panel ships many times a day; a
cache would serve yesterday's app and call it a feature.

It caches exactly one thing: a page to show when the panel cannot be reached.
Without it the installed app is a white screen with no URL bar, no reload
button and nothing that says what went wrong, so an unreachable panel and a
broken app look identical from the outside. That page also listens for the
network coming back and reloads itself, because the alternative is remembering
to reopen the app at the right moment.

Only navigations are intercepted. Everything else is left to the browser,
which fetches it exactly the way this would have.
*/

const SHELL = "clique-shell";
const OFFLINE = "offline.html";

const PAGE = `<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>CLIque</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; min-height:100dvh; display:grid; place-items:center;
         background:#0E1116; color:#E6E9EF; text-align:center;
         padding:24px calc(24px + env(safe-area-inset-right)) calc(24px + env(safe-area-inset-bottom))
                 calc(24px + env(safe-area-inset-left));
         font:16px/1.5 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  main { max-width:min(340px,86vw); }
  h1 { font-size:19px; margin:0 0 10px; font-weight:600; }
  p { margin:0 0 20px; color:#98A2B3; }
  button { font:inherit; font-weight:600; color:#fff; background:#A855F7;
           border:0; border-radius:4px; padding:11px 22px; min-height:44px; cursor:pointer; }
</style>
<main>
  <h1>Cannot reach CLIque</h1>
  <p>The panel did not answer. It may be stopped, or this device may not be on
     the network it runs on.</p>
  <button type="button" onclick="location.reload()">Try again</button>
</main>
<script>
  // Comes back on its own once there is a network again, so a dropped VPN or a
  // walk out of wifi range does not need anyone to notice and reopen the app.
  addEventListener("online", () => location.reload());
</script>
`;

self.addEventListener("install", (event) => {
  event.waitUntil((async () => {
    const cache = await caches.open(SHELL);
    await cache.put(
      OFFLINE,
      new Response(PAGE, { headers: { "content-type": "text/html; charset=utf-8" } })
    );
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    fetch(event.request).catch(async () => {
      const cache = await caches.open(SHELL);
      return (await cache.match(OFFLINE)) || Response.error();
    })
  );
});
