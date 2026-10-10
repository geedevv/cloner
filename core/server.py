"""Local http:// server + launchers for the http:// and file:// versions."""
import functools
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from .runtime import RANGE_SERVER_SRC

_ns = {}
exec(compile(RANGE_SERVER_SRC, '<range_server>', 'exec'), _ns)
RangeHandler, ThreadedServer = _ns['RangeHandler'], _ns['ThreadedServer']


def find_index(directory: Path):
    directory = Path(directory)
    idx = directory / 'index.html'
    if idx.exists():
        return idx
    for p in sorted(directory.glob('*.html')):
        return p
    for p in sorted(directory.rglob('index.html')):
        if '.stealth-cloner' not in p.parts:
            return p
    return None


class SiteServer:
    def __init__(self):
        self.httpd = None
        self.dir = None
        self.port = None

    def start(self, directory: Path, port: int = 8000) -> int:
        self.stop()
        directory = Path(directory)
        for p in range(port, port + 60):
            try:
                self.httpd = ThreadedServer(('127.0.0.1', p),
                                            functools.partial(RangeHandler, directory=str(directory)))
                break
            except OSError:
                continue
        if self.httpd is None:
            raise RuntimeError('No free port for the local server')
        self.dir, self.port = directory, self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return self.port

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = self.dir = self.port = None

    @property
    def running(self):
        return self.httpd is not None


def open_file_version(directory: Path):
    idx = find_index(directory)
    if not idx:
        return None
    webbrowser.open(idx.resolve().as_uri())
    return idx


def open_http_version(server: SiteServer, directory: Path):
    if not find_index(directory):
        return None
    if not (server.running and server.dir == Path(directory)):
        server.start(directory)
    url = f"http://127.0.0.1:{server.port}/"
    webbrowser.open(url)
    return url


def open_folder(directory: Path):
    directory = Path(directory)
    if sys.platform.startswith('win'):
        os.startfile(str(directory))       # type: ignore[attr-defined]
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', str(directory)])
    else:
        subprocess.Popen(['xdg-open', str(directory)])
