"""Post-processing tools: static (file://-safe) copy, zip, library scan."""
import json
import shutil
from pathlib import Path

from bs4 import BeautifulSoup

from .runtime import RESCUE_CSS
from .urlutil import human_size


def dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in Path(p).rglob('*') if f.is_file())


def read_manifest(project: Path):
    try:
        return json.loads((Path(project) / '.stealth-cloner' / 'manifest.json').read_text('utf-8'))
    except Exception:
        return None


def list_projects(base: Path):
    out = []
    base = Path(base)
    if not base.exists():
        return out
    for d in sorted(base.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if d.is_dir() and (d / '.stealth-cloner' / 'manifest.json').exists():
            m = read_manifest(d) or {}
            out.append({'name': d.name, 'path': d, 'manifest': m, 'size': dir_size(d)})
    return out


def _strip_html(path: Path):
    soup = BeautifulSoup(path.read_bytes(), 'html.parser')
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
    for img in soup.find_all('img'):
        ds = img.get('data-src') or img.get('data-lazy-src') or img.get('data-original')
        src = img.get('src') or ''
        if ds and (not src or src.startswith('data:')):
            img['src'] = ds
        if (img.get('loading') or '').lower() == 'lazy':
            img['loading'] = 'eager'
    if soup.head is not None and not soup.find(id='clone-rescue'):
        st = soup.new_tag('style')
        st['id'] = 'clone-rescue'
        st.string = RESCUE_CSS
        soup.head.append(st)
    path.write_bytes(str(soup).encode('utf-8'))


def make_static_copy(src: Path, dst: Path, progress=None):
    """Copy a clone and remove all scripts -> opens perfectly from file://."""
    src, dst = Path(src), Path(dst)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
        '.stealth-cloner', 'sw.js', '__clone_*', '__url_map.json', 'clone_log_*.log',
        'serve.py', 'serve.bat', '*.part', '*.tmp'))
    files = [p for p in dst.rglob('*') if p.suffix.lower() in ('.html', '.htm')]
    for i, p in enumerate(files, 1):
        try:
            _strip_html(p)
        except Exception:
            pass
        if progress:
            progress(i, len(files))
    return len(files)


def zip_project(src: Path) -> Path:
    src = Path(src)
    return Path(shutil.make_archive(str(src), 'zip', src))
