"""Stealth Cloner Pro engine.

Priority-queue crawler:  phase 1 = the site itself (pages + everything each page
needs to render: images, video, icons, fonts, CSS, JS, animation data),
phase 2 = linked files on the same site (pdf, docs...), phase 3 = other sites.
Nothing from a later phase runs while earlier-phase work is still queued.
"""
import asyncio
import base64
import contextlib
import json
import mimetypes
import os
import random
import re
import shutil
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse, unquote
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup

from .config import CloneConfig
from .filters import ScanRules
from .render import RenderPool
from .runtime import (SHIM_JS, SW_JS, SW_REGISTER_JS, SERVE_PY, SERVE_BAT, RESCUE_CSS)
from .urlutil import (PathMapper, normalize_url, category_of, ext_of, HTML_EXTS,
                      STATIC_EXTS, human_size)

# --------------------------------------------------------------------------- consts
QUEUED, DOWN, OK, FAIL, SKIP = 'queued', 'downloading', 'ok', 'failed', 'skipped'
PHASE_NAMES = {1: 'core', 2: 'files', 3: 'external'}

CSS_REF_RE = re.compile(
    r'@import\s+url\(\s*(?P<q1>[\'"]?)(?P<u1>[^\'")]+)(?P=q1)\s*\)'
    r'|@import\s+(?P<q2>[\'"])(?P<u2>[^\'"]+)(?P=q2)'
    r'|url\(\s*(?P<q3>[\'"]?)(?P<u3>[^\'")]+)(?P=q3)\s*\)', re.I)
SRCSET_SPLIT = re.compile(r'\s*,\s+|\s*,(?=[^\s])(?![^()]*\))')

EXT_ALT = ('jpg|jpeg|png|gif|webp|avif|svg|ico|bmp|mp4|webm|ogv|mov|m4v|mp3|ogg|wav|m4a|'
           'woff2|woff|ttf|otf|eot|json|lottie|glb|gltf|m3u8|vtt|css|js|mjs')
TEXT_URL_RE = re.compile(
    r'(?P<u>(?:https?:)?(?:\\?/){2}[A-Za-z0-9.\-]+(?::\d+)?'
    r'(?:[\\/][^\s"\'<>(){}\[\]|^`]*?)?\.(?:' + EXT_ALT + r'))'
    r'(?P<q>\?[^\s"\'<>(){}\[\]\\|^`,]*)?(?![A-Za-z0-9_])', re.I)
URL_ONLY_RE = re.compile(
    r'^\s*(?:(?:https?:)?//|/|\.{1,2}/)?[^\s"\'<>{}|\\^`]+\.(?:' + EXT_ALT.replace('|css|js|mjs', '') +
    r')(?:[?#][^\s"\'<>]*)?\s*$', re.I)

TARGETS = [
    ('a', 'href', 'page'), ('area', 'href', 'page'),
    ('link', 'href', 'link'), ('script', 'src', 'asset'),
    ('img', 'src', 'asset'), ('img', 'srcset', 'srcset'),
    ('image', 'href', 'asset'), ('image', 'xlink:href', 'asset'),
    ('source', 'src', 'asset'), ('source', 'srcset', 'srcset'),
    ('video', 'src', 'asset'), ('video', 'poster', 'asset'),
    ('audio', 'src', 'asset'), ('track', 'src', 'asset'),
    ('embed', 'src', 'asset'), ('object', 'data', 'asset'),
    ('input', 'src', 'asset'),
    ('iframe', 'src', 'frame'), ('frame', 'src', 'frame'),
    ('form', 'action', 'form'),
]
LINK_ASSET_RELS = {'stylesheet', 'icon', 'shortcut', 'apple-touch-icon',
                   'apple-touch-icon-precomposed', 'preload', 'modulepreload', 'manifest',
                   'mask-icon', 'image_src', 'fluid-icon', 'prefetch'}
HANDLED_ATTRS = {'src', 'href', 'srcset', 'data', 'poster', 'action', 'style', 'class', 'id',
                 'integrity', 'crossorigin', 'alt', 'title', 'name', 'type', 'rel', 'media',
                 'sizes', 'lang', 'target', 'width', 'height', 'loading', 'decoding', 'role',
                 'xlink:href'}
EMBED_EXTS = {'.json', '.xml', '.txt', '.csv', '.vtt', '.srt', '.svg', '.lottie', '.glb',
              '.gltf', '.bin', '.wasm', '.geojson', '.webmanifest', '.glsl', '.obj'}
MIME_FIX = {'.lottie': 'application/zip', '.glb': 'model/gltf-binary',
            '.gltf': 'model/gltf+json', '.wasm': 'application/wasm', '.json': 'application/json',
            '.vtt': 'text/vtt', '.svg': 'image/svg+xml'}


class TooBig(Exception):
    pass


def _decode(data: bytes) -> str:
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('latin-1', errors='replace')


class CloneEngine:
    def __init__(self, ctl, cfg: CloneConfig):
        self.ctl = ctl
        self.cfg = cfg.normalize()
        self.start_url = normalize_url(cfg.url, cfg.url) or cfg.url
        pu = urlparse(self.start_url)
        self.origin = f"{pu.scheme}://{pu.netloc}"
        self.root = cfg.project_dir()
        self.mapper = PathMapper(self.root, pu.netloc)
        self.rules = ScanRules(cfg.scan_rules)

        self.queue = asyncio.PriorityQueue()
        self._seq = 0
        self.workers = []
        self.session = None
        self.pool = None

        self.seen_pages = set()
        self.visited_paths = set()
        self.query_variants = {}
        self.asset_state = {}
        self.asset_paths = {}
        self.url_map = {}
        self.path_to_url = {}          # rel path -> (original url, 'page'|'asset')
        self.embedded = {}
        self.embed_bytes = 0
        self.robots = {}
        self.robots_lock = asyncio.Lock()
        self.host_sems = {}
        self.svg_cache = {}
        self.hls_count = 0
        self.phase_idx = 0
        self.inflight = {1: 0, 2: 0, 3: 0}
        self.last_progress = time.time()
        self.flushed = False
        self.halted = False
        self.deadline = (time.time() + cfg.max_time_min * 60) if cfg.max_time_min > 0 else None
        self.audit_info = {}

    # ------------------------------------------------------------------ helpers
    def log(self, msg, level='INFO', cat='SYS'):
        self.ctl.log(msg, level, cat)

    def _stop(self) -> bool:
        return self.ctl.stop_event.is_set()

    def _put(self, phase, tier, depth, kind, payload):
        self._seq += 1
        self.queue.put_nowait((phase, tier, depth, self._seq, kind, payload))

    def _register(self, url, path, kind):
        k = self.mapper.key(path)
        if k:
            self.path_to_url.setdefault(k, (url, kind))

    def _asset_path(self, url, hint=None):
        p = self.asset_paths.get(url)
        if p is None:
            p = self.mapper.filepath(url, 'asset', hint)
            self.asset_paths[url] = p
        return p

    def _host_sem(self, url):
        h = urlparse(url).netloc
        s = self.host_sems.get(h)
        if s is None:
            s = self.host_sems[h] = asyncio.Semaphore(self.cfg.per_host_conn)
        return s

    def _headers(self, url, referer=None, page=False):
        cfg = self.cfg
        h = {'Accept-Language': cfg.accept_language}
        ref = referer or cfg.default_referer
        if ref:
            h['Referer'] = ref
            if category_of(url) in ('font', 'code', 'other'):
                pr = urlparse(ref)
                h['Origin'] = f"{pr.scheme}://{pr.netloc}"
        if cfg.custom_user_agent:
            h['User-Agent'] = cfg.custom_user_agent
        if page:
            h['Accept'] = 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8'
        elif category_of(url) == 'image':
            h['Accept'] = 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8'
        for line in cfg.extra_headers.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                h[k.strip()] = v.strip()
        return h

    def _make_session(self):
        from curl_cffi.requests import AsyncSession
        cfg = self.cfg
        kw = dict(timeout=cfg.asset_timeout_s, max_clients=max(10, cfg.concurrency * 2))
        if not cfg.custom_user_agent and cfg.impersonate:
            kw['impersonate'] = cfg.impersonate
        if cfg.proxy:
            kw['proxies'] = {'http': cfg.proxy, 'https': cfg.proxy}
        try:
            return AsyncSession(**kw)
        except TypeError:
            kw.pop('max_clients', None)
            return AsyncSession(**kw)

    async def _polite_delay(self, light=False):
        d = self.cfg.request_delay_ms / 1000.0
        if d > 0:
            await asyncio.sleep(d * (0.25 if light else 1.0) * (0.7 + random.random() * 0.6))

    # ------------------------------------------------------------------ filters
    def _allow_asset(self, url) -> bool:
        if not self.rules.allowed(url):
            return False
        c, cfg = category_of(url), self.cfg
        return not ((c == 'image' and not cfg.get_images) or
                    (c == 'video' and not cfg.get_video) or
                    (c == 'audio' and not cfg.get_audio) or
                    (c == 'archive' and not cfg.get_archives) or
                    (c == 'document' and not cfg.get_documents))

    def _page_ok(self, url) -> bool:
        cfg = self.cfg
        if url in self.seen_pages or len(self.seen_pages) >= cfg.max_pages:
            return False
        if not self.rules.allowed(url):
            return False
        p = urlparse(url)
        segs = [s for s in p.path.split('/') if s]
        if len(segs) > cfg.max_path_depth:
            return False
        if len(segs) > 4 and max(Counter(segs).values()) >= 4:      # /a/b/a/b/a/b/ loops
            return False
        if p.query:
            n = self.query_variants.get(p.path, 0)
            if n >= cfg.max_query_variants:
                return False
            self.query_variants[p.path] = n + 1
        return True

    def _enqueue_page(self, url, depth, level=0) -> bool:
        if not self._page_ok(url):
            return False
        self.seen_pages.add(url)
        if level:
            depth = self.cfg.max_depth
        self._put(1 if level == 0 else 3, 1, depth, 'page',
                  {'url': url, 'depth': depth, 'level': level})
        return True

    def _enqueue_asset(self, url, referer, hint=None, phase=1) -> bool:
        st = self.asset_state
        if url in st or len(st) >= self.cfg.max_assets:
            return False
        if not self._allow_asset(url):
            st[url] = SKIP
            self.ctl.stats.skipped += 1
            return False
        st[url] = QUEUED
        self._asset_path(url, hint)
        self._put(phase, 0, 0, 'asset', {'url': url, 'referer': referer})
        return True

    def _local(self, raw, base_url, from_dir, kind='asset', hint=None):
        """(relative ref incl. fragment, absolute url, local path) or None."""
        absolute = normalize_url(raw, base_url)
        if not absolute:
            return None
        frag = urlparse(urljoin(base_url, raw.strip())).fragment
        ap = self._asset_path(absolute, hint) if kind == 'asset' else self.mapper.filepath(absolute, kind)
        self._register(absolute, ap, kind)
        rel = self.mapper.rel(ap, from_dir) + ('#' + frag if frag else '')
        return rel, absolute, ap

    # ------------------------------------------------------------------ robots
    async def _robots(self, origin):
        if origin in self.robots:
            return self.robots[origin]
        async with self.robots_lock:
            if origin in self.robots:
                return self.robots[origin]
            rp = None
            try:
                r = await self.session.get(f"{origin}/robots.txt", timeout=12,
                                           headers=self._headers(origin + '/robots.txt'))
                if r.status_code == 200 and r.text:
                    rp = RobotFileParser()
                    rp.parse(r.text.splitlines())
                    self.log(f"robots.txt loaded for {origin}", 'OK', 'ROBOTS')
            except Exception:
                pass
            self.robots[origin] = rp
            return rp

    async def _robots_ok(self, url) -> bool:
        try:
            p = urlparse(url)
            rp = await self._robots(f"{p.scheme}://{p.netloc}")
            return True if rp is None else rp.can_fetch('*', url)
        except Exception:
            return True

    # ------------------------------------------------------------------ run flow
    def _set_phase(self, phase):
        if phase > self.phase_idx and phase in PHASE_NAMES:
            self.phase_idx = phase
            self.ctl.stats.phase = PHASE_NAMES[phase]
            labels = {1: 'CORE SITE (pages + everything they need)',
                      2: 'LINKED FILES (same-site documents)',
                      3: 'EXTERNAL (other sites)'}
            self.log(f"── Phase {phase}: {labels[phase]} ──", 'INFO', 'PHASE')

    def _flush_queue(self):
        while True:
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except asyncio.QueueEmpty:
                break

    def _limit_reason(self):
        cfg, st = self.cfg, self.ctl.stats
        if self._stop():
            return 'Stop requested.'
        if not self.ctl.pause_event.is_set():
            self.last_progress = time.time()
            return None
        if self.deadline and time.time() > self.deadline:
            return f"Time limit reached ({cfg.max_time_min} min)."
        if st.total_bytes > cfg.max_total_mb * 1024 * 1024:
            return f"Total size limit reached ({cfg.max_total_mb} MB)."
        if (st.active > 0 and cfg.stall_timeout_s > 0 and
                time.time() - self.last_progress > cfg.stall_timeout_s):
            return f"No progress for {cfg.stall_timeout_s}s — stopping crawl."
        return None

    async def _drain(self):
        join = asyncio.ensure_future(self.queue.join())
        try:
            while not join.done():
                await asyncio.wait({join}, timeout=1.0)
                if not self.flushed:
                    reason = self._limit_reason()
                    if reason:
                        self.flushed = True
                        self.halted = True
                        self.log(reason + ' Finishing what is in flight.', 'WARN')
                        self._flush_queue()
        finally:
            if not join.done():
                join.cancel()

    async def _worker(self):
        while True:
            item = await self.queue.get()
            try:
                if not self.flushed and not self._stop():
                    await self._run_job(item)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.log(f"Job error: {e!r}", 'ERROR')
            finally:
                self.ctl.stats.done += 1
                self.ctl.stats.queued = self.queue.qsize()
                self.queue.task_done()

    def _asset_timeout(self, url):
        cfg = self.cfg
        if category_of(url) in ('video', 'audio', 'archive'):
            return cfg.media_timeout_s + 30
        return cfg.asset_timeout_s * cfg.retries + 30

    async def _run_job(self, item):
        phase, _tier, _depth, _seq, kind, p = item
        await self.ctl.pause_event.wait()
        if self._stop():
            return
        if phase > 1 and any(self.inflight.get(q, 0) for q in range(1, phase)):
            await asyncio.sleep(0.08)          # earlier phase still working: requeue
            self._put(phase, _tier, _depth, kind, p)
            return
        self._set_phase(phase)
        st = self.ctl.stats
        st.active += 1
        self.inflight[phase] = self.inflight.get(phase, 0) + 1
        try:
            if kind == 'page':
                await asyncio.wait_for(self._page_job(p), self.cfg.page_timeout_s * 2 + 90)
            else:
                await asyncio.wait_for(self._asset_job(p), self._asset_timeout(p['url']))
        except asyncio.TimeoutError:
            self._fail(p['url'], 'timeout')
        finally:
            st.active -= 1
            self.inflight[phase] -= 1
            self.last_progress = time.time()

    def _fail(self, url, reason):
        self.ctl.stats.errors += 1
        self.asset_state[url] = FAIL
        self.ctl.add_failure(url, reason)
        self.log(f"{reason}: {url}", 'ERROR', 'FAIL')

    async def run(self):
        cfg, ctl = self.cfg, self.ctl
        started = datetime.now()
        self.root.mkdir(parents=True, exist_ok=True)
        ctl.open_log(self.root)
        self.log('=== Stealth Cloner Pro v9 starting ===', 'INFO')
        self.log(f"Target: {self.start_url}", 'INFO')
        self.log(f"Output: {self.root}", 'INFO')
        self.log(f"depth={cfg.max_depth} pages<={cfg.max_pages} js={cfg.js_render} "
                 f"strip_scripts={cfg.strip_scripts} concurrency={cfg.concurrency}", 'INFO')
        self.session = self._make_session()
        try:
            if cfg.js_render:
                self.log(f"Launching Chromium ({cfg.js_workers} worker(s))…", 'INFO', 'JS')
                try:
                    self.pool = RenderPool(cfg)
                    await self.pool.start()
                    self.log('Chromium ready.', 'OK', 'JS')
                except Exception as e:
                    self.pool = None
                    self.log(f"Chromium unavailable ({e}). Falling back to plain HTTP. "
                             f"Run:  playwright install chromium", 'ERROR', 'JS')

            self.workers = [asyncio.create_task(self._worker()) for _ in range(cfg.concurrency)]
            self._set_phase(1)
            self._enqueue_page(self.start_url, 0, 0)
            if cfg.use_sitemap:
                await self._sitemap()
            await self._drain()

            if cfg.verify_pass and not self._stop():
                ctl.stats.phase = 'verify'
                await self._audit()
            ctl.stats.phase = 'finish'
            await asyncio.to_thread(self._finalize)
        finally:
            for w in self.workers:
                w.cancel()
            await asyncio.gather(*self.workers, return_exceptions=True)
            if self.pool:
                await self.pool.close()
            with contextlib.suppress(Exception):
                await self.session.close()

        secs = (datetime.now() - started).total_seconds()
        st = ctl.stats
        self.log('─' * 56, 'INFO')
        self.log(f"DONE in {secs:.0f}s — pages {st.pages}, assets {st.assets}, "
                 f"{human_size(st.total_bytes)}, errors {st.errors}, "
                 f"repaired {st.repaired}", 'OK' if st.errors == 0 else 'WARN')
        self.log('Open the clone with the file:// or http:// buttons.', 'INFO')
        ctl.last_result = f"{st.pages} pages · {st.assets} assets · {human_size(st.total_bytes)}"

    # ------------------------------------------------------------------ sitemap
    async def _sitemap(self):
        self.log('Scanning sitemaps…', 'INFO', 'SITEMAP')
        queue = [f"{self.origin}/sitemap.xml", f"{self.origin}/sitemap_index.xml",
                 f"{self.origin}/wp-sitemap.xml"]
        rp = await self._robots(self.origin)
        with contextlib.suppress(Exception):
            if rp is not None and rp.site_maps():
                queue += list(rp.site_maps())
        seen, added = set(), 0
        while queue and len(seen) < 60 and added < 5000 and not self._stop():
            sm = queue.pop(0)
            if sm in seen:
                continue
            seen.add(sm)
            data, _ct, _f, sc = await self._http_get_bytes(sm, timeout=25)
            if not data:
                continue
            if data[:2] == b'\x1f\x8b':
                import gzip
                try:
                    data = gzip.decompress(data)
                except Exception:
                    continue
            text = _decode(data)
            locs = re.findall(r'<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>', text, re.S)
            if '<sitemapindex' in text:
                queue += [u for u in locs if u not in seen]
                continue
            for u in locs:
                nu = normalize_url(u, self.origin)
                if nu and self.mapper.same_site(nu) and self._enqueue_page(nu, 1, 0):
                    added += 1
                    if added >= 5000:
                        break
        self.log(f"Sitemap added {added} page(s).", 'OK', 'SITEMAP')

    # ------------------------------------------------------------------ http
    async def _http_get_bytes(self, url, referer=None, timeout=None):
        cfg = self.cfg
        last = (None, '', url, 0)
        for attempt in range(cfg.retries):
            if self._stop():
                break
            try:
                async with self._host_sem(url):
                    r = await self.session.get(url, headers=self._headers(url, referer, page=True),
                                               timeout=timeout or cfg.asset_timeout_s)
                sc = r.status_code
                if sc == 200:
                    return r.content, (r.headers.get('content-type') or '').lower(), str(r.url), 200
                last = (None, '', url, sc)
                if sc in (429, 500, 502, 503, 504):
                    ra = r.headers.get('retry-after')
                    await asyncio.sleep(min(int(ra) if ra and ra.isdigit() else 2 ** attempt, 30))
                    continue
                break
            except Exception:
                last = (None, '', url, 0)
                await asyncio.sleep(1 + attempt)
        return last

    async def _download_to(self, url, tmp: Path, referer, timeout, max_bytes):
        """Stream to a temp file. Returns (status, ctype, size, retry_after)."""
        async with self._host_sem(url):
            r = await self.session.get(url, headers=self._headers(url, referer),
                                       timeout=timeout, stream=True)
            try:
                status = r.status_code
                ctype = (r.headers.get('content-type') or '').lower()
                if status != 200:
                    return status, ctype, 0, r.headers.get('retry-after')
                size = 0
                with open(tmp, 'wb') as f:
                    async for chunk in r.aiter_content():
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise TooBig()
                        f.write(chunk)
                return 200, ctype, size, None
            finally:
                with contextlib.suppress(Exception):
                    await r.aclose()

    @staticmethod
    def _write_file(path: Path, data: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + '.tmp')
        tmp.write_bytes(data)
        os.replace(tmp, path)

    # ------------------------------------------------------------------ asset jobs
    def _mark_ok(self, url, path: Path, size: int):
        st = self.ctl.stats
        self.asset_state[url] = OK
        st.assets += 1
        st.total_bytes += size
        self.ctl.add_file(path.name, size)
        k = self.mapper.key(path)
        if k:
            self.url_map[url] = k
        self.last_progress = time.time()

    async def _asset_job(self, p):
        url, referer = p['url'], p.get('referer')
        cfg = self.cfg
        if self.asset_state.get(url) in (OK, DOWN):
            return
        path = self._asset_path(url)
        self.asset_state[url] = DOWN
        with contextlib.suppress(Exception):
            if path.exists() and path.stat().st_size > 0:
                self._mark_ok(url, path, 0)
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        cat = category_of(url)
        timeout = cfg.media_timeout_s if cat in ('video', 'audio', 'archive') else cfg.asset_timeout_s
        max_bytes = cfg.max_file_mb * 1024 * 1024
        tmp = path.with_name(path.name + '.part')
        result, reason = None, 'download failed'

        for attempt in range(cfg.retries):
            if self._stop():
                return
            await self.ctl.pause_event.wait()
            try:
                await self._polite_delay(light=True)
                status, ctype, size, retry_after = await self._download_to(
                    url, tmp, referer, timeout, max_bytes)
            except TooBig:
                self.asset_state[url] = SKIP
                self.ctl.stats.skipped += 1
                self.log(f"Skipped (over {cfg.max_file_mb} MB): {url}", 'WARN', 'ASSET')
                with contextlib.suppress(Exception):
                    tmp.unlink()
                return
            except Exception as e:
                reason = f"error {type(e).__name__}"
                await asyncio.sleep(min(1 + attempt * 2, 8))
                continue
            if status == 200:
                result = (ctype, size)
                break
            reason = f"HTTP {status}"
            if status in (401, 403, 451) and self.pool:
                data, ct = await self.pool.fetch(url, referer)
                if data:
                    tmp.write_bytes(data)
                    result = (ct, len(data))
                    break
                break
            if status in (404, 410):
                break
            if status in (429, 500, 502, 503, 504):
                ra = int(retry_after) if retry_after and str(retry_after).isdigit() else 2 ** attempt
                await asyncio.sleep(min(ra, 30))
                continue
            break

        if result is None:
            with contextlib.suppress(Exception):
                tmp.unlink()
            self._fail(url, reason)
            return

        ctype, size = result
        if cat in ('image', 'font', 'video', 'audio') and 'text/html' in (ctype or ''):
            with contextlib.suppress(Exception):
                tmp.unlink()
            self._fail(url, 'server returned HTML instead of the file')
            return

        ext = path.suffix.lower()
        text_like = ext in ('.css', '.m3u8', '.js', '.mjs') or 'text/css' in (ctype or '') \
            or 'mpegurl' in (ctype or '')
        if text_like and size < 8 * 1024 * 1024:
            data = tmp.read_bytes()
            with contextlib.suppress(Exception):
                tmp.unlink()
            await self._save_bytes_asset(url, path, data, ctype)
        else:
            os.replace(tmp, path)
            self._mark_ok(url, path, size)
            self.log(f"Saved {path.name} ({human_size(size)})", 'OK', 'ASSET')

    async def _save_bytes_asset(self, url, path: Path, data: bytes, ctype=''):
        ext, ct = path.suffix.lower(), (ctype or '').lower()
        try:
            if ext == '.css' or 'text/css' in ct:
                data = self._rewrite_css(_decode(data), url, path.parent, url).encode('utf-8')
            elif (ext == '.m3u8' or 'mpegurl' in ct) and self.cfg.fetch_hls:
                data = self._rewrite_m3u8(_decode(data), url, path.parent, url).encode('utf-8')
            elif (ext in ('.js', '.mjs') and self.cfg.detect_hidden_links
                  and not self.cfg.strip_scripts and len(data) < 3_000_000):
                self._discover_in_text(_decode(data), url)
        except Exception as e:
            self.log(f"Post-process failed for {path.name}: {e}", 'WARN', 'ASSET')
        await asyncio.to_thread(self._write_file, path, data)
        self._mark_ok(url, path, len(data))
        self.log(f"Saved {path.name} ({human_size(len(data))})", 'OK', 'ASSET')

    # ------------------------------------------------------------------ css / hls / text
    def _rewrite_css(self, text, base_url, css_dir: Path, referer):
        def repl(m):
            if m.group('u1') is not None:
                raw, tmpl, hint = m.group('u1').strip(), '@import url("{}")', '.css'
            elif m.group('u2') is not None:
                raw, tmpl, hint = m.group('u2').strip(), '@import "{}"', '.css'
            else:
                raw, tmpl, hint = m.group('u3').strip(), 'url({})', None
            if not raw or raw.startswith(('data:', '#', 'about:', 'blob:', 'javascript:')):
                return m.group(0)
            ref = self._local(raw, base_url, css_dir, 'asset', hint)
            if not ref:
                return m.group(0)
            rel, absolute, _ap = ref
            if not self._allow_asset(absolute):
                return m.group(0)
            self._enqueue_asset(absolute, referer, hint)
            return tmpl.format(rel)
        return CSS_REF_RE.sub(repl, text)

    def _rewrite_m3u8(self, text, base_url, pl_dir: Path, referer):
        def ref(raw):
            raw = raw.strip()
            if self.hls_count >= 8000:
                return raw
            r = self._local(raw, base_url, pl_dir, 'asset')
            if not r:
                return raw
            self.hls_count += 1
            self._enqueue_asset(r[1], referer)
            return r[0]
        out = []
        for line in text.splitlines():
            s = line.strip()
            if not s:
                out.append(line)
            elif s.startswith('#'):
                out.append(re.sub(r'URI="([^"]+)"', lambda m: f'URI="{ref(m.group(1))}"', line)
                           if 'URI="' in s else line)
            else:
                out.append(ref(s))
        return '\n'.join(out) + '\n'

    def _discover_in_text(self, text, base_url):
        n = 0
        for m in TEXT_URL_RE.finditer(text):
            raw = (m.group('u') + (m.group('q') or '')).replace('\\/', '/')
            if raw.startswith('//'):
                raw = urlparse(base_url).scheme + ':' + raw
            a = normalize_url(raw, base_url)
            if a and self.mapper.same_site(a) and self._enqueue_asset(a, base_url):
                n += 1
                if n >= 300:
                    break

    def _sweep_text(self, text, base_url, from_dir: Path):
        def repl(m):
            raw = m.group('u') + (m.group('q') or '')
            esc = '\\/' in raw
            url = raw.replace('\\/', '/')
            if url.startswith('//'):
                url = urlparse(base_url).scheme + ':' + url
            a = normalize_url(url, base_url)
            if not a or not self._allow_asset(a):
                return m.group(0)
            ap = self._asset_path(a)
            self._register(a, ap, 'asset')
            self._enqueue_asset(a, base_url)
            rel = self.mapper.rel(ap, from_dir)
            return rel.replace('/', '\\/') if esc else rel
        return TEXT_URL_RE.sub(repl, text)

    # ------------------------------------------------------------------ pages
    @staticmethod
    def _is_html(ctype, data):
        if 'html' in ctype:
            return True
        if not ctype or 'text/plain' in ctype:
            return data[:512].lstrip().lower().startswith((b'<!doctype', b'<html', b'<'))
        return False

    async def _page_job(self, p):
        url, depth, level = p['url'], p['depth'], p['level']
        cfg, st = self.cfg, self.ctl.stats
        fp = self.mapper.filepath(url, 'page')
        if fp in self.visited_paths:
            return
        self.visited_paths.add(fp)
        if cfg.respect_robots and not await self._robots_ok(url):
            self.log(f"Blocked by robots.txt: {url}", 'WARN', 'ROBOTS')
            st.skipped += 1
            return
        await self._polite_delay()
        st.current_url = url

        html, base_url, captured, extra = None, url, [], []
        if cfg.js_render and self.pool:
            try:
                self.log(f"Render d{depth}: {url}", 'INFO', 'JS')
                r = await self.pool.render(url)
                if r.status >= 400:
                    self._fail(url, f"HTTP {r.status}")
                    return
                html, base_url = r.html, (r.final_url or url)
                captured, extra = r.captured, r.extra_urls
            except Exception as e:
                self.log(f"Render failed ({type(e).__name__}) — using HTTP: {url}", 'WARN', 'JS')
                html = None
        if html is None:
            data, ctype, final, sc = await self._http_get_bytes(url, timeout=cfg.page_timeout_s)
            if data is None:
                self._fail(url, f"HTTP {sc}" if sc else 'connection failed')
                return
            base_url = final or url
            if not self._is_html(ctype, data):
                await asyncio.to_thread(self._write_file, fp, data)
                self.asset_state[url] = OK
                self._mark_ok(url, fp, len(data))
                return
            html = data
        low = html[:200000].lower() if isinstance(html, (bytes, str)) else ''
        if (b'cf-chl' in low if isinstance(low, bytes) else 'cf-chl' in low) and \
                ('just a moment' in _decode(low) if isinstance(low, bytes) else 'just a moment' in low):
            self._fail(url, 'bot-challenge page (not saved)')
            return

        await self._process_html(url, base_url, html, depth, level)
        await self._ingest_captured(url, captured)
        for u in extra:
            n = normalize_url(u, base_url)
            if n:
                self._enqueue_asset(n, url)

    async def _ingest_captured(self, page_url, captured):
        cfg = self.cfg
        for c in captured:
            norm = normalize_url(c['url'], self.start_url)
            if not norm or norm == page_url:
                continue
            rt, body = c['rt'], c['body']
            if (rt in ('xhr', 'fetch') and cfg.capture_api and not cfg.strip_scripts
                    and body is not None and c.get('method', 'GET') == 'GET'):
                self._embed(norm, c['ctype'], body)
            if self.asset_state.get(norm) not in (None, QUEUED):
                continue
            if not self._allow_asset(norm):
                continue
            hint = '.js' if rt == 'script' else '.css' if rt == 'stylesheet' else None
            path = self._asset_path(norm, hint)
            self._register(norm, path, 'asset')
            if body is not None and rt != 'media' and len(body) > 0:
                self.asset_state[norm] = DOWN
                await self._save_bytes_asset(norm, path, body, c['ctype'])
            else:
                self._enqueue_asset(norm, page_url, hint)

    def _embed(self, url, ctype, body: bytes, cap=None):
        if not body or len(body) > (cap or 3 * 1024 * 1024):
            return
        if self.embed_bytes + len(body) > 80 * 1024 * 1024:
            return
        norm = normalize_url(url, self.start_url)
        if not norm or norm in self.embedded:
            return
        ct = (ctype or '').split(';')[0].strip().lower()
        text = ct.startswith('text/') or 'json' in ct or 'xml' in ct or 'javascript' in ct
        e = {'t': ctype or 'application/octet-stream', 's': 200}
        if text:
            try:
                e['x'] = body.decode('utf-8')
            except UnicodeDecodeError:
                e['b'] = base64.b64encode(body).decode('ascii')
        else:
            e['b'] = base64.b64encode(body).decode('ascii')
        self.embedded[norm] = e
        self.embed_bytes += len(body)
        self.ctl.stats.embedded = len(self.embedded)

    # ------------------------------------------------------------------ html processing
    def _strip_scripts(self, soup):
        for s in soup.find_all('script'):
            if (s.get('type') or '').lower() not in ('application/ld+json', 'application/json'):
                s.decompose()
        for l in soup.find_all('link'):
            rel = l.get('rel') or []
            rels = {r.lower() for r in ([rel] if isinstance(rel, str) else rel)}
            if 'modulepreload' in rels or (rels & {'preload', 'prefetch'} and
                                           (l.get('as') or '').lower() == 'script'):
                l.decompose()
        for tag in soup.find_all(True):
            for a in list(tag.attrs):
                if len(a) > 2 and a[:2].lower() == 'on' and a.isalpha():
                    del tag[a]

    def _handle_attr(self, tag, attr, kind, base_url, from_dir, depth, level):
        val = tag.get(attr)
        if not val or not isinstance(val, str):
            return
        raw = val.strip()
        absolute = normalize_url(raw, base_url)
        if not absolute:
            return
        full = urljoin(base_url, raw)
        same = self.mapper.same_site(absolute)
        cat = category_of(absolute)
        cfg = self.cfg
        hint = None

        if kind == 'form':
            tag[attr] = full
            return

        if kind == 'link':
            rel = tag.get('rel') or []
            rels = {r.lower() for r in ([rel] if isinstance(rel, str) else rel)}
            if not (rels & LINK_ASSET_RELS):
                tag[attr] = full
                return
            as_ = (tag.get('as') or '').lower()
            if 'stylesheet' in rels or as_ == 'style':
                hint = '.css'
            elif as_ == 'script' or 'modulepreload' in rels:
                hint = '.js'
            kind = 'asset'

        if kind in ('page', 'frame'):
            if cat in ('image', 'video', 'audio'):
                kind = 'asset'
            elif kind == 'page' and cat in ('archive', 'document', 'code', 'font', 'other'):
                wanted = self._allow_asset(absolute)
                if same and cfg.fetch_linked_files and wanted:
                    ph = 2
                elif not same and cfg.fetch_external and wanted:
                    ph = 3
                else:
                    tag[attr] = full
                    return
                ref = self._local(raw, base_url, from_dir, 'asset')
                if ref:
                    tag[attr] = ref[0]
                    self._enqueue_asset(ref[1], base_url, None, ph)
                return
            else:
                # HTML page
                if same:
                    if not self.rules.allowed(absolute):
                        tag[attr] = full
                        return
                    ref = self._local(raw, base_url, from_dir, 'page')
                    if ref:
                        tag[attr] = ref[0]
                        if depth + 1 <= cfg.max_depth:
                            self._enqueue_page(ref[1], depth + 1, level)
                elif (cfg.fetch_external and level < cfg.external_depth and kind == 'page'
                      and self.rules.allowed(absolute)):
                    ref = self._local(raw, base_url, from_dir, 'page')
                    if ref:
                        tag[attr] = ref[0]
                        self._enqueue_page(ref[1], depth, level + 1)
                else:
                    tag[attr] = full
                return

        # required asset (anything the page needs to render)
        if not self._allow_asset(absolute):
            tag[attr] = full
            return
        if attr == 'src' and tag.name == 'script':
            hint = '.js'
        ref = self._local(raw, base_url, from_dir, 'asset', hint)
        if ref:
            tag[attr] = ref[0]
            self._enqueue_asset(ref[1], base_url, hint)

    def _rewrite_srcset(self, srcset, base_url, from_dir):
        out = []
        for part in SRCSET_SPLIT.split(srcset):
            part = part.strip()
            if not part:
                continue
            bits = part.split(None, 1)
            desc = f" {bits[1]}" if len(bits) > 1 else ''
            ref = self._local(bits[0], base_url, from_dir)
            if not ref or not self._allow_asset(ref[1]):
                out.append(part)
                continue
            out.append(ref[0] + desc)
            self._enqueue_asset(ref[1], base_url)
        return ', '.join(out)

    async def _fetch_text(self, url, referer):
        if url in self.svg_cache:
            return self.svg_cache[url]
        data, _ct, _f, _sc = await self._http_get_bytes(url, referer=referer, timeout=25)
        text = _decode(data) if data else None
        self.svg_cache[url] = text
        return text

    async def _inline_sprites(self, soup, base_url):
        uses = [u for u in soup.find_all('use')
                if (u.get('href') or u.get('xlink:href') or '').strip()
                and not (u.get('href') or u.get('xlink:href')).strip().startswith('#')]
        if not uses:
            return
        symbols, done = [], set()
        for u in uses:
            attr = 'href' if u.get('href') else 'xlink:href'
            href = u.get(attr).strip()
            if '#' not in href:
                continue
            file_part, frag = href.split('#', 1)
            absu = normalize_url(file_part, base_url)
            if not absu or not frag:
                continue
            text = await self._fetch_text(absu, base_url)
            if not text:
                continue
            key = (absu, frag)
            if key not in done:
                try:
                    ss = BeautifulSoup(text, 'xml')
                    el = ss.find(id=frag)
                    if el is not None:
                        symbols.append(str(el))
                        done.add(key)
                except Exception:
                    continue
            if key in done:
                u[attr] = '#' + frag
        if symbols and soup.body:
            holder = BeautifulSoup(
                '<svg xmlns="http://www.w3.org/2000/svg" style="display:none" aria-hidden="true">'
                + ''.join(symbols) + '</svg>', 'html.parser')
            soup.body.insert(0, holder)

    def _sweep_generic(self, soup, base_url, from_dir):
        for tag in soup.find_all(True):
            if tag.name in ('meta', 'style'):
                continue
            for attr in list(tag.attrs):
                if attr in HANDLED_ATTRS:
                    continue
                v = tag.get(attr)
                if not isinstance(v, str) or len(v) < 5 or len(v) > 8000:
                    continue
                low = attr.lower()
                if 'srcset' in low or 'bgset' in low:
                    tag[attr] = self._rewrite_srcset(v, base_url, from_dir)
                elif '{' in v or '[' in v or '\\/' in v or ('http' in v and ',' in v):
                    nv = self._sweep_text(v, base_url, from_dir)
                    if nv != v:
                        tag[attr] = nv
                elif URL_ONLY_RE.match(v):
                    ref = self._local(v.strip(), base_url, from_dir)
                    if ref and self._allow_asset(ref[1]):
                        tag[attr] = ref[0]
                        self._enqueue_asset(ref[1], base_url)

    def _inject_head(self, soup, base_url, filepath: Path):
        head = soup.head
        if head is None:
            if soup.html is None:
                return
            head = soup.new_tag('head')
            soup.html.insert(0, head)
        for m in head.find_all('meta'):
            if m.get('charset') is not None or (m.get('http-equiv') or '').lower() == 'content-type':
                m.decompose()
        items = []
        cs = soup.new_tag('meta')
        cs['charset'] = 'utf-8'
        items.append(cs)
        if not self.cfg.strip_scripts:
            root = self.mapper.root_rel(filepath.parent)
            boot = soup.new_tag('script')
            boot.string = (f"window.__CLONE_PAGE_URL={json.dumps(base_url)};"
                           f"window.__CLONE_ROOT={json.dumps(root)};")
            items.append(boot)
            for name in ('__clone_map.js', '__clone_shim.js'):
                s = soup.new_tag('script')
                s['src'] = root + name
                items.append(s)
            sw = soup.new_tag('script')
            sw.string = SW_REGISTER_JS
            items.append(sw)
        else:
            css = soup.new_tag('style')
            css['id'] = 'clone-rescue'
            css.string = RESCUE_CSS
            items.append(css)
        note = soup.new_tag('meta')
        note['name'] = 'cloner-note'
        note['content'] = 'Archived with Stealth Cloner Pro v9'
        items.append(note)
        for i, el in enumerate(items):
            head.insert(i, el)

    async def _process_html(self, url, base_url, html, depth, level):
        cfg = self.cfg
        filepath = self.mapper.filepath(url, 'page')
        from_dir = filepath.parent
        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception as e:
            self._fail(url, f"parse error {e}")
            return

        for b in soup.find_all('base'):
            b.decompose()
        for t in soup.find_all(['link', 'script']):
            for a in ('integrity', 'crossorigin'):
                if t.has_attr(a):
                    del t[a]
        for m in soup.find_all('meta'):
            if (m.get('http-equiv') or '').lower() in (
                    'content-security-policy', 'content-security-policy-report-only',
                    'x-frame-options'):
                m.decompose()
        if cfg.strip_scripts:
            self._strip_scripts(soup)

        for tag_name, attr, kind in TARGETS:
            for tag in soup.find_all(tag_name):
                if kind == 'srcset':
                    v = tag.get(attr)
                    if v and isinstance(v, str):
                        tag[attr] = self._rewrite_srcset(v, base_url, from_dir)
                else:
                    self._handle_attr(tag, attr, kind, base_url, from_dir, depth, level)

        if cfg.detect_hidden_links:
            self._sweep_generic(soup, base_url, from_dir)
            if not cfg.strip_scripts:
                for s in soup.find_all('script'):
                    if s.get('src') or not s.string:
                        continue
                    t = (s.get('type') or '').lower()
                    if 'template' in t or 'x-tmpl' in t:
                        continue
                    nv = self._sweep_text(s.string, base_url, from_dir)
                    if nv != s.string:
                        s.string = nv
            else:
                for s in soup.find_all('script'):
                    if s.string:
                        nv = self._sweep_text(s.string, base_url, from_dir)
                        if nv != s.string:
                            s.string = nv

        for st in soup.find_all('style'):
            if st.string:
                st.string = self._rewrite_css(st.string, base_url, from_dir, url)
        for tag in soup.find_all(style=True):
            sv = tag.get('style') or ''
            if 'url(' in sv:
                tag['style'] = self._rewrite_css(sv, base_url, from_dir, url)

        if cfg.inline_svg_sprites:
            with contextlib.suppress(Exception):
                await self._inline_sprites(soup, base_url)

        if cfg.strip_scripts:
            for img in soup.find_all('img'):
                ds = img.get('data-src') or img.get('data-lazy-src') or img.get('data-original')
                src = img.get('src') or ''
                if ds and (not src or src.startswith('data:')):
                    img['src'] = ds
                if (img.get('loading') or '').lower() == 'lazy':
                    img['loading'] = 'eager'

        self._inject_head(soup, base_url, filepath)
        data = str(soup).encode('utf-8')
        try:
            await asyncio.to_thread(self._write_file, filepath, data)
        except Exception as e:
            self._fail(url, f"write failed {e}")
            return
        st = self.ctl.stats
        st.pages += 1
        st.total_bytes += len(data)
        self.ctl.add_file(filepath.name, len(data))
        k = self.mapper.key(filepath)
        if k:
            self.url_map[url] = k
            self.url_map[base_url] = k
            self._register(url, filepath, 'page')
        self.last_progress = time.time()
        self.log(f"Page saved d{depth}: {url} ({human_size(len(data))})", 'OK', 'PAGE')

    # ------------------------------------------------------------------ verify & repair
    def _resolve(self, file: Path, ref: str, root: Path):
        r = (ref or '').strip()
        if not r or r.startswith(('http:', 'https:', '//', 'data:', '#', 'mailto:', 'tel:',
                                  'javascript:', 'blob:', 'about:')):
            return None
        r = r.split('#', 1)[0].split('?', 1)[0]
        if not r or os.path.basename(r).startswith('__clone_') or os.path.basename(r) == 'sw.js':
            return None
        try:
            target = (file.parent / unquote(r)).resolve()
            return target, target.relative_to(root).as_posix()
        except Exception:
            return None

    def _audit_css_text(self, text, file: Path, root: Path, revert, missing):
        def repl(m):
            raw = (m.group('u1') or m.group('u2') or m.group('u3') or '').strip()
            res = self._resolve(file, raw, root)
            if not res or res[0].exists():
                return m.group(0)
            orig = self.path_to_url.get(res[1])
            missing.append({'file': str(file), 'rel': res[1], 'url': orig[0] if orig else None,
                            'kind': orig[1] if orig else 'asset'})
            if revert and orig:
                frag = ('#' + raw.split('#', 1)[1]) if '#' in raw else ''
                if m.group('u1') is not None:
                    return f'@import url("{orig[0]}{frag}")'
                if m.group('u2') is not None:
                    return f'@import "{orig[0]}{frag}"'
                return f'url({orig[0]}{frag})'
            return m.group(0)
        return CSS_REF_RE.sub(repl, text)

    def _audit_file(self, path: Path, root: Path, revert: bool):
        missing = []
        if path.suffix.lower() == '.css':
            text = path.read_text(encoding='utf-8', errors='replace')
            new = self._audit_css_text(text, path, root, revert, missing)
            if revert and new != text:
                path.write_text(new, encoding='utf-8')
            return missing
        soup = BeautifulSoup(path.read_bytes(), 'html.parser')
        changed = False
        for tag_name, attr, kind in TARGETS:
            for tag in soup.find_all(tag_name):
                val = tag.get(attr)
                if not val or not isinstance(val, str):
                    continue
                if kind == 'srcset':
                    parts, local_change = [], False
                    for cand in SRCSET_SPLIT.split(val):
                        cand = cand.strip()
                        if not cand:
                            continue
                        bits = cand.split(None, 1)
                        u, d = bits[0], (' ' + bits[1] if len(bits) > 1 else '')
                        res = self._resolve(path, u, root)
                        if res and not res[0].exists():
                            orig = self.path_to_url.get(res[1])
                            missing.append({'file': str(path), 'rel': res[1],
                                            'url': orig[0] if orig else None, 'kind': 'asset'})
                            if revert and orig:
                                u, local_change = orig[0], True
                        parts.append(u + d)
                    if local_change:
                        tag[attr] = ', '.join(parts)
                        changed = True
                    continue
                res = self._resolve(path, val, root)
                if not res or res[0].exists():
                    continue
                orig = self.path_to_url.get(res[1])
                missing.append({'file': str(path), 'rel': res[1], 'url': orig[0] if orig else None,
                                'kind': orig[1] if orig else 'asset'})
                if revert and orig:
                    frag = ('#' + val.split('#', 1)[1]) if '#' in val else ''
                    tag[attr] = orig[0] + frag
                    changed = True
        for st in soup.find_all('style'):
            if st.string:
                new = self._audit_css_text(st.string, path, root, revert, missing)
                if revert and new != st.string:
                    st.string = new
                    changed = True
        for tag in soup.find_all(style=True):
            sv = tag.get('style') or ''
            if 'url(' in sv:
                new = self._audit_css_text(sv, path, root, revert, missing)
                if revert and new != sv:
                    tag['style'] = new
                    changed = True
        if revert and changed:
            path.write_bytes(str(soup).encode('utf-8'))
        return missing

    def _audit_all(self, revert=False):
        root = self.root.resolve()
        missing = []
        for p in list(self.root.rglob('*')):
            if (p.is_file() and p.suffix.lower() in ('.html', '.htm', '.css')
                    and '.stealth-cloner' not in p.parts):
                try:
                    missing.extend(self._audit_file(p, root, revert))
                except Exception:
                    continue
        return missing

    async def _audit(self):
        cfg = self.cfg
        self.log('── Verify & repair: checking every local link ──', 'INFO', 'AUDIT')
        missing = await asyncio.to_thread(self._audit_all, False)
        first = len(missing)
        self.log(f"{first} missing reference(s) found.", 'INFO' if not first else 'WARN', 'AUDIT')
        for rnd in range(cfg.repair_rounds):
            if not missing or self.halted or self._stop():
                break
            urls = {m['url'] for m in missing if m['url'] and m['kind'] == 'asset'}
            if not urls:
                break
            self.log(f"Repair round {rnd + 1}: retrying {len(urls)} file(s)…", 'INFO', 'AUDIT')
            for u in urls:
                self.asset_state.pop(u, None)
                self._enqueue_asset(u, self.start_url)
            await self._drain()
            after = await asyncio.to_thread(self._audit_all, False)
            self.ctl.stats.repaired += max(0, len(missing) - len(after))
            missing = after
        final = await asyncio.to_thread(self._audit_all, True)
        unresolved = sorted({m['url'] for m in final if m['url']})
        self.audit_info = {'missing_initial': first, 'missing_final': len(final),
                           'repaired': self.ctl.stats.repaired,
                           'reverted_to_online_links': len(unresolved),
                           'sample': unresolved[:100]}
        if final:
            self.log(f"{len(final)} reference(s) could not be fetched; they now point to the "
                     f"original online URL instead of a broken local path.", 'WARN', 'AUDIT')
        else:
            self.log('All local references resolve. ✔', 'OK', 'AUDIT')

    # ------------------------------------------------------------------ finalize
    def _inline_fonts(self):
        rx = re.compile(r'url\(\s*([\'"]?)([^\'")]+)\1\s*\)')
        mimes = {'.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf',
                 '.otf': 'font/otf'}
        count = 0
        for css in self.root.rglob('*.css'):
            try:
                text = css.read_text(encoding='utf-8')
            except Exception:
                continue
            changed = False

            def repl(m):
                nonlocal changed, count
                ref = m.group(2).strip()
                if ref.startswith(('data:', 'http:', 'https:', '//')):
                    return m.group(0)
                clean = ref.split('#')[0].split('?')[0]
                mime = mimes.get(os.path.splitext(clean)[1].lower())
                if not mime:
                    return m.group(0)
                try:
                    fp = (css.parent / unquote(clean)).resolve()
                    if not fp.is_file() or fp.stat().st_size > 1_500_000:
                        return m.group(0)
                    b64 = base64.b64encode(fp.read_bytes()).decode('ascii')
                except Exception:
                    return m.group(0)
                changed = True
                count += 1
                return f'url(data:{mime};base64,{b64})'
            new = rx.sub(repl, text)
            if changed:
                css.write_text(new, encoding='utf-8')
        self.log(f"Inlined {count} font file reference(s).", 'OK', 'FONTS')

    def _embed_local_data(self):
        n = 0
        for url, rel in list(self.url_map.items()):
            ext = os.path.splitext(rel)[1].lower()
            if ext not in EMBED_EXTS or url in self.embedded:
                continue
            try:
                fp = self.root / rel
                if not fp.is_file() or fp.stat().st_size > 6 * 1024 * 1024:
                    continue
                ct = MIME_FIX.get(ext) or mimetypes.guess_type(rel)[0] or 'application/octet-stream'
                self._embed(url, ct, fp.read_bytes(), cap=6 * 1024 * 1024)
                n += 1
            except Exception:
                continue
        return n

    def _finalize(self):
        cfg = self.cfg
        meta = self.root / '.stealth-cloner'
        meta.mkdir(parents=True, exist_ok=True)
        if cfg.inline_fonts:
            with contextlib.suppress(Exception):
                self._inline_fonts()
        if not cfg.strip_scripts:
            with contextlib.suppress(Exception):
                self._embed_local_data()
            base = {'base': self.origin, 'm': self.url_map}
            mp = dict(base)
            mp['k'] = {u: 1 for u in self.embedded}
            (self.root / '__clone_map.js').write_text(
                'window.__CLONE_DATA=' + json.dumps(mp) + ';', encoding='utf-8')
            (self.root / '__clone_blobs.js').write_text(
                'window.__CLONE_DATA=window.__CLONE_DATA||{};window.__CLONE_DATA.r='
                + json.dumps(self.embedded) + ';', encoding='utf-8')
            (self.root / '__url_map.json').write_text(json.dumps(base), encoding='utf-8')
            (self.root / '__clone_shim.js').write_text(SHIM_JS, encoding='utf-8')
            (self.root / 'sw.js').write_text(SW_JS, encoding='utf-8')
        (self.root / 'serve.py').write_text(SERVE_PY, encoding='utf-8')
        (self.root / 'serve.bat').write_text(SERVE_BAT, encoding='utf-8')
        st = self.ctl.stats
        by = Counter(self.asset_state.values())
        manifest = {
            'tool': 'Stealth Cloner Pro', 'version': '9.0', 'source': self.start_url,
            'created': datetime.now().isoformat(timespec='seconds'),
            'pages': st.pages, 'assets': st.assets, 'bytes': st.total_bytes,
            'errors': st.errors, 'embedded_responses': len(self.embedded),
            'resources_by_status': dict(by), 'audit': self.audit_info,
            'mode': 'snapshot (scripts removed)' if cfg.strip_scripts else 'full (scripts kept)',
            'config': {k: getattr(cfg, k) for k in ('max_depth', 'js_render', 'strip_scripts',
                                                    'fetch_external', 'fetch_linked_files')},
        }
        (meta / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        (meta / 'refs.json').write_text(
            json.dumps({k: v[0] for k, v in self.path_to_url.items()}), encoding='utf-8')
        (meta / 'failures.json').write_text(json.dumps(self.ctl.failures[-500:], indent=2),
                                            encoding='utf-8')
        self.log('Wrote runtime files, manifest and audit report.', 'OK', 'MANIFEST')
