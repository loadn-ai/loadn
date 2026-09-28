// loadn service worker：只为满足 PWA 安装条件。
// 刻意不拦截任何请求（不缓存、不 respondWith）——SSE 流与构建产物更新完全不受影响。
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => { /* no-op：老版 Chrome 安装检测要求注册过 fetch */ });
