"""Clone configuration, presets and persistence."""
import json
from dataclasses import dataclass, fields, asdict
from pathlib import Path

APP_DIR = Path.home() / '.stealth_cloner'
CFG_FILE = APP_DIR / 'config.json'

DEFAULT_RULES = ("-*doubleclick.net* -*googletagmanager.com* -*google-analytics.com* "
                 "-*facebook.net/tr* -*hotjar.com* -*clarity.ms* -*connect.facebook.net*")


@dataclass
class CloneConfig:
    # ---- target
    url: str = 'https://example.com'
    project_name: str = 'my_clone'
    output_dir: str = str(Path.home() / 'clones')
    preset: str = 'balanced'

    # ---- limits
    max_depth: int = 6
    external_depth: int = 1
    max_pages: int = 1500
    max_assets: int = 30000
    max_file_mb: int = 300
    max_total_mb: int = 4000
    max_time_min: int = 60
    max_query_variants: int = 3
    max_path_depth: int = 14

    # ---- flow control
    concurrency: int = 12
    per_host_conn: int = 6
    js_workers: int = 2
    retries: int = 3
    request_delay_ms: int = 0
    page_timeout_s: int = 90
    asset_timeout_s: int = 45
    media_timeout_s: int = 300
    stall_timeout_s: int = 180

    # ---- behaviour
    respect_robots: bool = True
    use_sitemap: bool = True
    fetch_linked_files: bool = True    # phase 2: same-site pdf/zip/... files
    fetch_external: bool = False       # phase 3: other sites (after everything else)
    get_images: bool = True
    get_video: bool = True
    get_audio: bool = True
    get_documents: bool = True
    get_archives: bool = False
    scan_rules: str = DEFAULT_RULES

    # ---- content fidelity
    detect_hidden_links: bool = True   # scan inline scripts / JSON / data-* for asset URLs
    inline_svg_sprites: bool = True
    fetch_hls: bool = True
    capture_api: bool = True           # embed fetch/XHR responses for file:// replay
    inline_fonts: bool = True
    verify_pass: bool = True
    repair_rounds: int = 2

    # ---- rendering
    js_render: bool = True
    strip_scripts: bool = False
    post_load_ms: int = 1500
    scroll_pass: bool = True
    viewport_w: int = 1440
    viewport_h: int = 900

    # ---- identity / network
    impersonate: str = 'chrome124'
    custom_user_agent: str = ''
    accept_language: str = 'en-US,en;q=0.9'
    extra_headers: str = ''
    default_referer: str = ''
    proxy: str = ''

    def normalize(self):
        for f in fields(self):
            v = getattr(self, f.name)
            try:
                if f.type is int:
                    v = int(float(v)) if v not in (None, '') else int(f.default)
                elif f.type is bool:
                    v = bool(v)
                elif f.type is str:
                    v = '' if v is None else str(v)
            except Exception:
                v = f.default
            setattr(self, f.name, v)
        self.url = self.url.strip()
        if self.url and '://' not in self.url:
            self.url = 'https://' + self.url
        self.project_name = ''.join(
            c if (c.isalnum() or c in '-_.') else '_' for c in self.project_name.strip()) or 'clone'
        self.concurrency = max(1, min(64, self.concurrency))
        self.js_workers = max(1, min(8, self.js_workers))
        self.per_host_conn = max(1, min(32, self.per_host_conn))
        self.retries = max(1, min(8, self.retries))
        return self

    def project_dir(self) -> Path:
        return Path(self.output_dir) / self.project_name

    def reset_defaults(self):
        keep = {k: getattr(self, k) for k in ('url', 'project_name', 'output_dir')}
        for f in fields(self):
            setattr(self, f.name, f.default)
        for k, v in keep.items():
            setattr(self, k, v)

    def apply_preset(self, name: str):
        self.reset_defaults()
        self.preset = name
        for k, v in PRESETS.get(name, {}).items():
            setattr(self, k, v)


PRESETS = {
    'balanced': {},
    'max_fidelity': dict(js_workers=3, post_load_ms=2500, max_depth=8, repair_rounds=3,
                         fetch_linked_files=True, capture_api=True, fetch_hls=True,
                         max_pages=3000, media_timeout_s=600),
    'fast_static': dict(js_render=False, concurrency=24, capture_api=False,
                        post_load_ms=0, scroll_pass=False),
    'snapshot_file': dict(js_render=True, strip_scripts=True, capture_api=False,
                          inline_fonts=True, post_load_ms=2500),
    'single_page': dict(max_depth=0, max_pages=1, use_sitemap=False,
                        fetch_linked_files=False, fetch_external=False),
}
PRESET_LABELS = {
    'balanced': 'Balanced (recommended)',
    'max_fidelity': 'Max fidelity (slow, thorough)',
    'fast_static': 'Fast static (no JS rendering)',
    'snapshot_file': 'Snapshot — scripts removed (best for file://)',
    'single_page': 'Single page + everything it needs',
}


def load_config() -> CloneConfig:
    cfg = CloneConfig()
    try:
        if CFG_FILE.exists():
            data = json.loads(CFG_FILE.read_text(encoding='utf-8'))
            names = {f.name for f in fields(cfg)}
            for k, v in data.items():
                if k in names:
                    setattr(cfg, k, v)
    except Exception:
        pass
    return cfg.normalize()


def save_config(cfg: CloneConfig):
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CFG_FILE.write_text(json.dumps(asdict(cfg), indent=2), encoding='utf-8')
        return True
    except Exception:
        return False
