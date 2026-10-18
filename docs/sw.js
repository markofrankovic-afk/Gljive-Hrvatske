/* Service worker: aplikacija radi i bez interneta.
   - Ljuska aplikacije: spremljena unaprijed, uvijek dostupna.
   - Fotografije s Wikipedije i kartice karte: spremaju se dok ih gledaš, s ograničenjem broja.
   - Analiza slike i vremenski podaci: uvijek s mreže. */
const VERSION = "gh-v2.0";
const SHELL = ["./", "index.html", "manifest.webmanifest", "vendor/leaflet.js", "vendor/leaflet.css",
  "vendor/images/marker-icon.png", "vendor/images/marker-shadow.png", "vendor/images/layers.png",
  "icons/icon-192.png", "icons/icon-512.png"];
const LIMITS = { "gh-tiles": 1500, "gh-photos": 400 };

self.addEventListener("install", e => {
  e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(k => k.startsWith("gh-v") && k !== VERSION).map(k => caches.delete(k))
  )).then(() => self.clients.claim()));
});

async function trim(name) {
  const max = LIMITS[name]; if (!max) return;
  const c = await caches.open(name), keys = await c.keys();
  for (let i = 0; i < keys.length - max; i++) await c.delete(keys[i]);
}
async function networkFirst(req, name) {
  const c = await caches.open(name);
  try { const res = await fetch(req); if (res.ok) c.put(req, res.clone()); return res; }
  catch (e) { return (await c.match(req)) || Response.error(); }
}
async function cacheFirst(req, name) {
  const c = await caches.open(name), hit = await c.match(req);
  if (hit) return hit;
  try {
    const res = await fetch(req);
    if (res.ok || res.type === "opaque") { c.put(req, res.clone()); trim(name); }
    return res;
  } catch (e) { return hit || Response.error(); }
}

self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET") return;
  if (url.pathname.includes("/api/") || url.hostname.endsWith("open-meteo.com")) return;
  if (url.origin === location.origin && url.pathname.includes("/model/") && url.pathname.endsWith(".json")) { e.respondWith(networkFirst(req, "gh-model")); return; }
  if (url.origin === location.origin && (url.pathname.includes("/model/") || url.pathname.includes("/vendor/ort/"))) { e.respondWith(cacheFirst(req, "gh-model")); return; }
  if (url.hostname === "tile.openstreetmap.org") { e.respondWith(cacheFirst(req, "gh-tiles")); return; }
  if (url.hostname.endsWith("wikipedia.org") || url.hostname === "upload.wikimedia.org") { e.respondWith(cacheFirst(req, "gh-photos")); return; }
  if (url.origin === location.origin) {
    e.respondWith(caches.match(req).then(hit => hit || fetch(req).then(res => {
      if (res.ok) { const copy = res.clone(); caches.open(VERSION).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => caches.match("index.html"))));
  }
});
