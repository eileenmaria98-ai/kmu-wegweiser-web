/* Offline-Modus: Seite und eigene Dateien werden gespeichert.
   Seite: zuerst Netz (damit Änderungen sofort ankommen), offline aus dem Speicher.
   Bei jeder inhaltlichen Änderung VERSION erhöhen. */
const VERSION = "kmu-wegweiser-v10";
const FILES = [
  "./",
  "index.html",
  "data.json",
  "manifest.webmanifest",
  "assets/logo-gewerbeverband.svg",
  "assets/bildmarke-gewerbeverband.svg",
  "assets/icons/apple-touch-icon.png",
  "assets/icons/icon-192.png",
  "assets/icons/icon-512.png"
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET" || new URL(req.url).origin !== self.location.origin) return;
  /* Seite und Inhalte (data.json): zuerst Netz, offline aus dem Speicher. */
  const isData = new URL(req.url).pathname.endsWith("/data.json");
  if (req.mode === "navigate" || isData) {
    const key = isData ? "data.json" : "index.html";
    e.respondWith(
      fetch(req)
        .then((res) => { const copy = res.clone(); caches.open(VERSION).then((c) => c.put(key, copy)); return res; })
        .catch(() => caches.match(key))
    );
    return;
  }
  e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
});
