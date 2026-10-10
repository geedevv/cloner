"""Files injected into every clone so it behaves like the original offline
(both from file:// and from http://localhost)."""

SHIM_JS = r"""
/* Stealth Cloner Pro - runtime shim (file:// and http://) */
(function () {
  if (window.__CLONE_SHIM__) return;
  window.__CLONE_SHIM__ = 1;
  var D = window.__CLONE_DATA || (window.__CLONE_DATA = {base: '', m: {}, k: {}});
  var PAGE = window.__CLONE_PAGE_URL || location.href;
  var ROOT = window.__CLONE_ROOT || './';
  var LO = location.origin;
  var IS_HTTP = location.protocol.indexOf('http') === 0;

  function toAbs(u) {
    try {
      if (u && typeof u === 'object' && u.url) u = u.url;
      u = String(u);
      if (!u || /^(data|blob|javascript|about|mailto|tel):|^#/i.test(u)) return null;
      var h = new URL(u, PAGE).href.split('#')[0];
      if (LO && LO !== 'null' && h.indexOf(LO + '/') === 0) h = (D.base || '') + h.slice(LO.length);
      return h;
    } catch (e) { return null; }
  }
  function localFor(abs) {
    var p = D.m && D.m[abs];
    return p ? ROOT + p.split('/').map(encodeURIComponent).join('/') : null;
  }
  function mapUrl(u) {
    try {
      if (u == null || u === '') return u;
      var a = toAbs(u), l = a && localFor(a);
      return l || u;
    } catch (e) { return u; }
  }
  function mapSrcset(v) {
    return String(v).split(',').map(function (part) {
      var t = part.trim(); if (!t) return part;
      var i = t.search(/\s/), u = i < 0 ? t : t.slice(0, i), rest = i < 0 ? '' : t.slice(i);
      return mapUrl(u) + rest;
    }).join(', ');
  }

  /* ---- lazily loaded embedded responses ---- */
  var blobsP = null;
  function loadBlobs() {
    if (D.r) return Promise.resolve();
    if (!blobsP) blobsP = new Promise(function (res) {
      var s = document.createElement('script');
      s.src = ROOT + '__clone_blobs.js';
      s.onload = s.onerror = function () { if (!D.r) D.r = {}; res(); };
      (document.head || document.documentElement).appendChild(s);
    });
    return blobsP;
  }
  function bin(e) {
    var s = atob(e.b), a = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) a[i] = s.charCodeAt(i);
    return a;
  }
  function bodyOf(e) { return e.x !== undefined ? e.x : bin(e); }

  /* ---- fetch ---- */
  var of = window.fetch;
  if (of) window.fetch = function (input, init) {
    try {
      var method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
      if (method === 'GET') {
        var k = toAbs(input);
        if (k && D.k && D.k[k]) {
          return loadBlobs().then(function () {
            var e = D.r && D.r[k];
            if (!e) return of.call(window, input, init);
            return new Response(bodyOf(e), {status: e.s || 200, statusText: 'OK',
              headers: {'Content-Type': e.t || 'application/octet-stream'}});
          });
        }
        if (IS_HTTP && (typeof input === 'string' || input instanceof URL)) {
          var lu = k && localFor(k);
          if (lu) return of.call(window, lu, init);
        }
      }
    } catch (err) {}
    return of.apply(this, arguments);
  };

  /* ---- XMLHttpRequest ---- */
  var XO = XMLHttpRequest.prototype.open, XS = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) {
    this.__cm = String(m || 'GET').toUpperCase();
    this.__cu = u;
    if (IS_HTTP && this.__cm === 'GET') {
      var a = toAbs(u), l = a && !(D.k && D.k[a]) && localFor(a);
      if (l) arguments[1] = l;
    }
    return XO.apply(this, arguments);
  };
  XMLHttpRequest.prototype.send = function () {
    var x = this, args = arguments;
    if (x.__cm === 'GET') {
      var k = toAbs(x.__cu);
      if (k && D.k && D.k[k]) {
        loadBlobs().then(function () {
          var e = D.r && D.r[k];
          if (!e) return XS.apply(x, args);
          setTimeout(function () {
            var txt = e.x !== undefined ? e.x : '';
            var rt = x.responseType, val;
            if (rt === 'arraybuffer') val = (e.x !== undefined ? new TextEncoder().encode(e.x) : bin(e)).buffer;
            else if (rt === 'blob') val = new Blob([bodyOf(e)], {type: e.t});
            else if (rt === 'json') { try { val = JSON.parse(txt); } catch (_) { val = null; } }
            else val = txt;
            function def(n, v) { Object.defineProperty(x, n, {configurable: true, get: function () { return v; }}); }
            def('readyState', 4); def('status', e.s || 200); def('statusText', 'OK');
            def('responseText', txt); def('response', val); def('responseURL', k);
            x.getResponseHeader = function (h) { return /^content-type$/i.test(h) ? e.t : null; };
            x.getAllResponseHeaders = function () { return 'content-type: ' + e.t + '\r\n'; };
            ['readystatechange', 'load', 'loadend'].forEach(function (n) {
              try { x.dispatchEvent(new Event(n)); } catch (_) {}
            });
          }, 0);
        });
        return;
      }
    }
    return XS.apply(this, arguments);
  };

  /* ---- src / href / poster / srcset assigned by JS -> local copies ---- */
  function patchProp(proto, prop, mapper) {
    try {
      var d = Object.getOwnPropertyDescriptor(proto, prop);
      if (!d || !d.set) return;
      Object.defineProperty(proto, prop, {configurable: true, enumerable: d.enumerable, get: d.get,
        set: function (v) { d.set.call(this, mapper(v)); }});
    } catch (e) {}
  }
  [[window.HTMLImageElement, 'src'], [window.HTMLSourceElement, 'src'],
   [window.HTMLMediaElement, 'src'], [window.HTMLScriptElement, 'src'],
   [window.HTMLIFrameElement, 'src'], [window.HTMLEmbedElement, 'src'],
   [window.HTMLLinkElement, 'href'], [window.HTMLVideoElement, 'poster']
  ].forEach(function (p) { if (p[0]) patchProp(p[0].prototype, p[1], mapUrl); });
  [[window.HTMLImageElement, 'srcset'], [window.HTMLSourceElement, 'srcset']
  ].forEach(function (p) { if (p[0]) patchProp(p[0].prototype, p[1], mapSrcset); });

  var SA = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function (n, v) {
    try {
      var ln = String(n).toLowerCase(), tn = this.tagName;
      if (/^(IMG|SOURCE|VIDEO|AUDIO|SCRIPT|IFRAME|EMBED|TRACK)$/.test(tn) && (ln === 'src' || ln === 'poster')) v = mapUrl(v);
      else if (tn === 'LINK' && ln === 'href') v = mapUrl(v);
      else if (/^(IMG|SOURCE)$/.test(tn) && ln === 'srcset') v = mapSrcset(v);
    } catch (e) {}
    return SA.call(this, n, v);
  };
})();
"""

SW_JS = r"""
// Stealth Cloner Pro - offline service worker (http:// only)
const scope = self.registration.scope;
const origin = new URL(scope).origin;
let data = null;
const ready = fetch(new URL('__url_map.json', scope))
  .then(r => r.json()).then(d => { data = d; })
  .catch(() => { data = {base: '', m: {}}; });

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith((async () => {
    await ready;
    const u = e.request.url.split('#')[0];
    const m = data.m || {};
    try {
      if (m[u]) return await fetch(new URL(m[u].split('/').map(encodeURIComponent).join('/'), scope));
      if (u.indexOf(origin + '/') === 0) {
        try { const d = await fetch(e.request); if (d && d.ok) return d; } catch (_) {}
        const alt = (data.base || '').replace(/\/$/, '') + u.slice(origin.length);
        if (m[alt]) return await fetch(new URL(m[alt].split('/').map(encodeURIComponent).join('/'), scope));
        return new Response('', {status: 404});
      }
      return await fetch(e.request);
    } catch (err) {
      return new Response('', {status: 504, statusText: 'offline'});
    }
  })());
});
"""

SW_REGISTER_JS = (
    "if('serviceWorker' in navigator && location.protocol.indexOf('http')===0){"
    "var r=window.__CLONE_ROOT||'./';"
    "navigator.serviceWorker.register(new URL(r+'sw.js',location.href).href,"
    "{scope:new URL(r,location.href).href})"
    ".then(function(){return navigator.serviceWorker.ready;})"
    ".then(function(){try{if(!navigator.serviceWorker.controller&&!sessionStorage.__swr)"
    "{sessionStorage.__swr=1;location.reload();}}catch(e){}})"
    ".catch(function(){});}"
)

# Injected in snapshot (no-JS) mode and in static copies: reveals content that
# normally waits for scripts to un-hide it.
RESCUE_CSS = (
    "[data-aos],[data-sal],[data-scroll],.aos-init,.wow,.reveal,.animate-on-scroll{"
    "opacity:1!important;transform:none!important;visibility:visible!important;transition:none!important}"
    ".elementor-invisible{visibility:visible!important;opacity:1!important}"
    ".lazyload,.lazy,.lazyloaded{opacity:1!important}"
    "#preloader,.preloader,.page-loader,.loader-wrap,.site-preloader,#loader,.pace,.pace-active{display:none!important}"
    "html,body{overflow:auto!important}"
)

RANGE_SERVER_SRC = r'''
import functools, http.server, os, re, socketserver, threading

class RangeHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
        '.js': 'text/javascript', '.mjs': 'text/javascript', '.wasm': 'application/wasm',
        '.woff2': 'font/woff2', '.woff': 'font/woff', '.svg': 'image/svg+xml',
        '.webp': 'image/webp', '.avif': 'image/avif', '.json': 'application/json',
        '.mp4': 'video/mp4', '.webm': 'video/webm', '.m3u8': 'application/vnd.apple.mpegurl',
        '.lottie': 'application/zip', '.glb': 'model/gltf-binary'}
    _range_left = None

    def log_message(self, *a):
        pass

    def handle(self):
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def end_headers(self):
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def send_head(self):
        rng = self.headers.get('Range')
        path = self.translate_path(self.path)
        if not rng or not os.path.isfile(path):
            return super().send_head()
        m = re.match(r'bytes=(\d*)-(\d*)', rng)
        if not m or (not m.group(1) and not m.group(2)):
            return super().send_head()
        try:
            f = open(path, 'rb')
        except OSError:
            return super().send_head()
        size = os.fstat(f.fileno()).st_size
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:
            start = max(0, size - int(m.group(2)))
            end = size - 1
        if start >= size or start > end:
            f.close()
            self.send_error(416, 'Requested Range Not Satisfiable')
            return None
        self.send_response(206)
        self.send_header('Content-Type', self.guess_type(path))
        self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, end, size))
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        f.seek(start)
        self._range_left = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        left = self._range_left
        if left is None:
            return super().copyfile(source, outputfile)
        while left > 0:
            buf = source.read(min(65536, left))
            if not buf:
                break
            outputfile.write(buf)
            left -= len(buf)
        self._range_left = None


class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
'''

SERVE_PY = '''"""Serve this clone locally:  python serve.py   ->  http://localhost:8000"""
import os, sys, webbrowser
''' + RANGE_SERVER_SRC + '''

if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    httpd = ThreadedServer(("127.0.0.1", port), functools.partial(RangeHandler, directory=here))
    print("Serving on http://localhost:%d  (Ctrl+C to stop)" % port)
    try:
        webbrowser.open("http://localhost:%d/" % port)
    except Exception:
        pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
'''

SERVE_BAT = "@echo off\r\ncd /d %~dp0\r\npython serve.py\r\npause\r\n"
