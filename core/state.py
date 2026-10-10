"""Shared application state (one clone at a time) + logging bus."""
import asyncio
import time
from datetime import datetime
from pathlib import Path

from .config import CloneConfig, load_config
from .urlutil import human_size


class Stats:
    def __init__(self):
        self.reset()

    def reset(self):
        self.pages = 0
        self.assets = 0
        self.errors = 0
        self.queued = 0
        self.active = 0
        self.done = 0
        self.total_bytes = 0
        self.skipped = 0
        self.embedded = 0
        self.repaired = 0
        self.current_url = ''
        self.phase = 'idle'
        self.elapsed = '—'
        self.rate = '—'


PHASES = [('core', 'Core site'), ('files', 'Linked files'),
          ('external', 'External'), ('verify', 'Verify & repair'), ('finish', 'Finalize')]


class Controller:
    def __init__(self):
        self.cfg: CloneConfig = load_config()
        self.stats = Stats()
        self.log_entries = []
        self.recent_files = []
        self.failures = []
        self.is_running = False
        self.is_paused = False
        self.stop_event = asyncio.Event()
        self.pause_event = asyncio.Event()
        self.pause_event.set()
        self.engine = None
        self.task = None
        self.started_at = None
        self.log_file = None
        self.log_file_path = None
        self.server = None          # SiteServer, created lazily
        self.server_port = None
        self.last_result = ''

    # ------------------------------------------------------------ logging
    def log(self, message: str, level: str = 'INFO', category: str = 'SYS'):
        ts = datetime.now().strftime('%H:%M:%S')
        entry = {'ts': ts, 'level': level.upper(), 'category': (category or 'SYS').upper(),
                 'msg': message}
        self.log_entries.append(entry)
        if len(self.log_entries) > 15000:
            del self.log_entries[:3000]
        if self.log_file:
            try:
                self.log_file.write(f"[{ts}] [{entry['level']}] [{entry['category']}] {message}\n")
                self.log_file.flush()
            except Exception:
                pass

    def add_file(self, name: str, size: int):
        self.recent_files.append({'name': name, 'size': int(size)})
        if len(self.recent_files) > 300:
            del self.recent_files[:100]

    def add_failure(self, url: str, reason: str):
        self.failures.append({'url': url, 'reason': reason})
        if len(self.failures) > 2000:
            del self.failures[:500]

    def open_log(self, project_dir: Path):
        try:
            self.close_log()
            self.log_file_path = project_dir / f"clone_log_{datetime.now():%Y%m%d_%H%M%S}.log"
            self.log_file = open(self.log_file_path, 'w', encoding='utf-8')
        except Exception:
            self.log_file = None

    def close_log(self):
        if self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None

    # ------------------------------------------------------------ status
    @property
    def status_text(self) -> str:
        if not self.is_running:
            return 'IDLE'
        label = dict(PHASES).get(self.stats.phase, self.stats.phase)
        return f"PAUSED · {label}" if self.is_paused else f"RUNNING · {label}"

    def tick(self):
        """Refresh derived stats (called from UI timers)."""
        s = self.stats
        if self.is_running and self.started_at:
            secs = int(time.time() - self.started_at)
            s.elapsed = f"{secs // 3600:d}:{secs % 3600 // 60:02d}:{secs % 60:02d}"
            s.rate = f"{human_size(s.total_bytes / max(1, secs))}/s"
        elif not self.is_running:
            s.rate = s.rate if s.rate != '—' else '—'
        if self.engine is not None and self.is_running:
            try:
                s.queued = self.engine.queue.qsize()
            except Exception:
                pass

    # ------------------------------------------------------------ control
    def reset_run(self):
        self.stats.reset()
        self.log_entries.clear()
        self.recent_files.clear()
        self.failures.clear()
        self.stop_event.clear()
        self.pause_event.set()
        self.is_paused = False

    async def start(self):
        if self.is_running:
            return False, 'A clone is already running.'
        cfg = self.cfg.normalize()
        if not cfg.url:
            return False, 'Enter a target URL first.'
        from .engine import CloneEngine   # lazy: avoids circular import
        self.reset_run()
        self.is_running = True
        self.started_at = time.time()
        self.engine = CloneEngine(self, CloneConfig(**cfg.__dict__))
        self.task = asyncio.create_task(self._runner())
        return True, 'Clone started.'

    async def _runner(self):
        try:
            await self.engine.run()
        except asyncio.CancelledError:
            self.log('Run cancelled.', 'WARN')
        except Exception as e:      # never leave the UI stuck in "running"
            self.log(f"Fatal engine error: {e!r}", 'ERROR')
        finally:
            self.is_running = False
            self.is_paused = False
            self.pause_event.set()
            self.stats.phase = 'idle'
            self.stats.current_url = ''
            self.close_log()

    def pause_toggle(self):
        if not self.is_running:
            return
        if self.is_paused:
            self.pause_event.set()
            self.is_paused = False
            self.log('Resumed.', 'OK')
        else:
            self.pause_event.clear()
            self.is_paused = True
            self.log('Paused — no new requests will start.', 'PENDING')

    def stop(self):
        if not self.is_running:
            return
        self.pause_event.set()
        self.stop_event.set()
        self.log('Stop requested — finishing in-flight downloads…', 'PENDING')


ctl = Controller()
