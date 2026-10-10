"""URL normalisation, categories and URL -> local path mapping."""
import hashlib
import os
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, quote, unquote

from yarl import URL

TRACKING_PARAMS = {
    'utm_source', 'utm_medium', 'utm_campaign', 'utm_term', 'utm_content',
    'fbclid', 'gclid', 'gclsrc', 'dclid', 'msclkid', 'yclid', '_openstat',
    'mc_eid', 'igshid', 'spm', 'ref_src',
}

CATEGORIES = {
    'image': {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.avif', '.svg', '.ico',
              '.bmp', '.tif', '.tiff', '.apng', '.jfif'},
    'video': {'.mp4', '.webm', '.ogv', '.mov', '.m4v', '.avi', '.mkv', '.m3u8',
              '.mpd', '.flv', '.wmv', '.mpg', '.mpeg'},
    'audio': {'.mp3', '.ogg', '.wav', '.m4a', '.aac', '.flac', '.opus'},
    'archive': {'.zip', '.rar', '.7z', '.gz', '.tar', '.tgz', '.bz2'},
    'document': {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
                 '.csv', '.txt', '.rtf', '.odt'},
    'font': {'.woff', '.woff2', '.ttf', '.otf', '.eot'},
    'code': {'.css', '.js', '.mjs', '.json', '.map', '.wasm', '.xml', '.lottie',
             '.glb', '.gltf', '.bin', '.vtt', '.srt', '.webmanifest'},
}
HTML_EXTS = {'.html', '.htm', '.xhtml'}
STATIC_EXTS = set().union(*CATEGORIES.values())

BAD_FS = re.compile(r'[<>:"|?*\x00-\x1f]')
RESERVED = {'con', 'prn', 'aux', 'nul'} | {f'com{i}' for i in range(1, 10)} | \
    {f'lpt{i}' for i in range(1, 10)}

SKIP_SCHEMES = ('javascript:', 'mailto:', 'tel:', '#', 'data:', 'blob:',
                'about:', 'chrome:', 'sms:', 'whatsapp:', 'intent:', 'ftp:')


def human_size(n) -> str:
    try:
        n = float(n)
    except Exception:
        return "0 B"
    if n <= 0:
        return "0 B"
    units = ['B', 'KB', 'MB', 'GB', 'TB']
    i = 0
    while n >= 1024 and i < len(units) - 1:
        n /= 1024.0
        i += 1
    return f"{int(n)} B" if i == 0 else f"{n:.2f} {units[i]}"


def bare_host(netloc: str) -> str:
    n = (netloc or '').lower()
    return n[4:] if n.startswith('www.') else n


def ext_of(url: str) -> str:
    try:
        return os.path.splitext(unquote(urlparse(url).path))[1].lower()
    except Exception:
        return ''


def category_of(url: str) -> str:
    e = ext_of(url)
    for c, s in CATEGORIES.items():
        if e in s:
            return c
    if e in HTML_EXTS:
        return 'html'
    return 'other' if e else 'none'


def strip_tracking(url: str) -> str:
    try:
        u = URL(url)
    except Exception:
        return url
    if not u.query:
        return str(u)
    kept = [(k, v) for k, v in u.query.items() if k.lower() not in TRACKING_PARAMS]
    return str(u.with_query(kept) if kept else u.with_query(None))


def normalize_url(url, base):
    """Absolute http(s) URL without fragment / tracking params, or None."""
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url or url.lower().startswith(SKIP_SCHEMES):
        return None
    try:
        u = URL(urljoin(base, url)).with_fragment(None)
    except Exception:
        return None
    if u.scheme not in ('http', 'https') or not u.host:
        return None
    return strip_tracking(str(u))


class PathMapper:
    def __init__(self, output_dir: Path, base_domain: str):
        self.root = Path(output_dir)
        self.base_domain = base_domain
        self.max_len = 225

    def same_site(self, url: str) -> bool:
        try:
            return bare_host(urlparse(url).netloc) == bare_host(self.base_domain)
        except Exception:
            return False

    @staticmethod
    def _safe(p: str) -> str:
        p = BAD_FS.sub('_', p).rstrip('. ') or '_'
        if p.lower().split('.')[0] in RESERVED:
            p = '_' + p
        if len(p) > 110:
            root, ext = os.path.splitext(p)
            h = hashlib.md5(p.encode('utf-8')).hexdigest()[:8]
            p = root[:80] + '_' + h + ext[:10]
        return p

    def filepath(self, url: str, kind: str = 'asset', force_ext: str = None) -> Path:
        p = urlparse(url)
        raw = unquote(p.path).lstrip('/')
        ext = os.path.splitext(raw)[1].lower()
        if not raw or raw.endswith('/'):
            raw += 'index.html'
        elif not ext:
            raw += force_ext or '/index.html'
        elif kind == 'page' and ext not in HTML_EXTS and ext not in STATIC_EXTS:
            raw += '.html'
        if p.query:
            h = hashlib.md5(p.query.encode('utf-8')).hexdigest()[:8]
            root, e2 = os.path.splitext(raw)
            raw = f"{root}_{h}{e2}" if e2 else f"{raw}_{h}"
        parts = [self._safe(x) for x in raw.replace('\\', '/').split('/')
                 if x not in ('', '.', '..')]
        if not parts:
            parts = ['index.html']
        if not self.same_site(url):
            parts = ['_external', self._safe(p.netloc.replace(':', '_'))] + parts
        full = self.root.joinpath(*parts)
        if len(str(full)) > self.max_len:
            e = os.path.splitext(parts[-1])[1][:10]
            h = hashlib.md5('/'.join(parts).encode('utf-8')).hexdigest()[:16]
            full = self.root / '_long' / (h + e)
        return full

    @staticmethod
    def rel(target: Path, from_dir: Path) -> str:
        try:
            r = os.path.relpath(target, from_dir).replace('\\', '/')
        except ValueError:
            r = str(target).replace('\\', '/')
        r = quote(r, safe="/._-~")
        return r if r.startswith('.') else './' + r

    def root_rel(self, from_dir: Path) -> str:
        try:
            r = os.path.relpath(self.root, from_dir).replace('\\', '/')
        except ValueError:
            return './'
        return './' if r == '.' else r.rstrip('/') + '/'

    def key(self, path: Path) -> str:
        try:
            return Path(path).relative_to(self.root).as_posix()
        except Exception:
            return ''
