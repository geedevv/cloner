"""Headless Chromium pool: renders pages and captures every network response."""
import asyncio
from dataclasses import dataclass, field

SAVE_TYPES_FULL = {'stylesheet', 'image', 'font', 'media', 'script', 'xhr', 'fetch',
                   'texttrack', 'manifest', 'other'}
SAVE_TYPES_STATIC = {'stylesheet', 'image', 'font', 'media', 'texttrack', 'manifest'}
BODY_CAP = 8 * 1024 * 1024
RENDER_BODY_TOTAL = 160 * 1024 * 1024


@dataclass
class RenderResult:
    html: str
    final_url: str
    status: int
    captured: list = field(default_factory=list)   # dicts: url, rt, status, ctype, body
    extra_urls: list = field(default_factory=list)  # currentSrc of media/img elements


class PageWorker:
    def __init__(self, pool, page):
        self.pool = pool
        self.page = page
        self.cap = []
        self.pending = []
        self.total = 0
        self._hook(page)

    def _hook(self, page):
        page.on('response', self._on_response)
        page.on('dialog', lambda d: asyncio.ensure_future(self._dismiss(d)))

    @staticmethod
    async def _dismiss(d):
        try:
            await d.dismiss()
        except Exception:
            pass

    def _on_response(self, response):
        try:
            req = response.request
            rt = req.resource_type
            if rt not in self.pool.save_types:
                return
            item = {'url': response.url, 'rt': rt, 'status': response.status,
                    'ctype': response.headers.get('content-type', ''), 'body': None,
                    'method': req.method}
            self.cap.append(item)
            if rt != 'media' and response.status == 200 and self.total < RENDER_BODY_TOTAL:
                self.pending.append(asyncio.ensure_future(self._grab(response, item)))
        except Exception:
            pass

    async def _grab(self, response, item):
        try:
            body = await response.body()
            if body and len(body) <= BODY_CAP:
                item['body'] = body
                self.total += len(body)
        except Exception:
            pass

    async def _scroll(self):
        js = """async () => {
            const sleep = ms => new Promise(r => setTimeout(r, ms));
            let last = -1, steps = 0;
            const step = Math.max(300, Math.floor((window.innerHeight || 800) * 0.8));
            while (steps < 60) {
                const h = Math.max(document.body ? document.body.scrollHeight : 0,
                                   document.documentElement.scrollHeight);
                window.scrollBy(0, step);
                steps++;
                await sleep(140);
                const y = window.scrollY + (window.innerHeight || 800);
                if (y >= h - 4) {
                    await sleep(350);
                    const h2 = Math.max(document.body ? document.body.scrollHeight : 0,
                                        document.documentElement.scrollHeight);
                    if (h2 === h || h2 === last) break;
                    last = h2;
                }
            }
            window.scrollTo(0, 0);
        }"""
        try:
            await self.page.evaluate(js)
        except Exception:
            pass

    async def _prime_media(self):
        js = """async () => {
            const vs = [...document.querySelectorAll('video')];
            for (const v of vs) { try { v.muted = true; v.preload = 'auto'; v.load();
                const p = v.play(); if (p && p.catch) p.catch(() => {}); } catch (e) {} }
            await new Promise(r => setTimeout(r, 1800));
            for (const v of vs) { try { v.pause(); v.currentTime = 0; } catch (e) {} }
            const out = [];
            document.querySelectorAll('video,audio,img,source,link[rel~=icon]').forEach(e => {
                const u = e.currentSrc || e.src || e.href; if (u && !u.startsWith('data:')) out.push(u);
            });
            return out;
        }"""
        try:
            return await self.page.evaluate(js) or []
        except Exception:
            return []

    async def run(self, url):
        cfg = self.pool.cfg
        page = self.page
        self.cap, self.pending, self.total = [], [], 0
        resp = await page.goto(url, wait_until='domcontentloaded',
                               timeout=cfg.page_timeout_s * 1000)
        status = resp.status if resp else 0
        for state, t in (('load', 12000), ('networkidle', 6000)):
            try:
                await page.wait_for_load_state(state, timeout=t)
            except Exception:
                pass
        await page.wait_for_timeout(max(0, cfg.post_load_ms))
        if cfg.scroll_pass:
            await self._scroll()
        extra = await self._prime_media()
        try:
            await page.wait_for_load_state('networkidle', timeout=4000)
        except Exception:
            pass
        await page.wait_for_timeout(500)
        html = await page.content()
        final = page.url
        if self.pending:
            await asyncio.gather(*self.pending, return_exceptions=True)
        cap, self.cap, self.pending = self.cap, [], []
        return RenderResult(html=html, final_url=final, status=status,
                            captured=cap, extra_urls=extra)


class RenderPool:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pw = None
        self.browser = None
        self.context = None
        self.q = asyncio.Queue()
        self.workers = []
        self.save_types = SAVE_TYPES_STATIC if cfg.strip_scripts else SAVE_TYPES_FULL

    async def start(self):
        from playwright.async_api import async_playwright
        cfg = self.cfg
        self.pw = await async_playwright().start()
        self.browser = await self.pw.chromium.launch(headless=True)
        kw = dict(viewport={'width': cfg.viewport_w, 'height': cfg.viewport_h},
                  ignore_https_errors=True, service_workers='block',
                  locale=(cfg.accept_language.split(',')[0] or 'en-US'))
        if cfg.custom_user_agent:
            kw['user_agent'] = cfg.custom_user_agent
        if cfg.proxy:
            kw['proxy'] = {'server': cfg.proxy}
        self.context = await self.browser.new_context(**kw)
        if cfg.extra_headers.strip():
            hdrs = {}
            for line in cfg.extra_headers.splitlines():
                if ':' in line:
                    k, v = line.split(':', 1)
                    hdrs[k.strip()] = v.strip()
            if hdrs:
                await self.context.set_extra_http_headers(hdrs)
        for _ in range(cfg.js_workers):
            page = await self.context.new_page()
            w = PageWorker(self, page)
            self.workers.append(w)
            self.q.put_nowait(w)

    async def render(self, url):
        w = await self.q.get()
        try:
            return await asyncio.wait_for(w.run(url), self.cfg.page_timeout_s * 2 + 30)
        except BaseException:
            # a stuck/crashed page is replaced so later renders stay healthy
            try:
                await w.page.close()
            except Exception:
                pass
            try:
                np = await self.context.new_page()
                w.page = np
                w._hook(np)
            except Exception:
                pass
            raise
        finally:
            self.q.put_nowait(w)

    async def fetch(self, url, referer=None, timeout_ms=45000):
        """Download through the browser context (cookies + browser TLS/headers)."""
        try:
            hdrs = {'Referer': referer} if referer else {}
            r = await self.context.request.get(url, headers=hdrs, timeout=timeout_ms)
            if r.ok:
                return await r.body(), r.headers.get('content-type', '')
        except Exception:
            pass
        return None, ''

    async def close(self):
        for obj, meth in ((self.context, 'close'), (self.browser, 'close'), (self.pw, 'stop')):
            if obj:
                try:
                    await getattr(obj, meth)()
                except Exception:
                    pass
