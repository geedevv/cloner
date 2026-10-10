"""Stealth Cloner Pro v9 — entry point.   python main.py  ->  http://127.0.0.1:8080"""
import os

from nicegui import ui

import views.dashboard   # noqa: F401  (registers pages)
import views.settings    # noqa: F401
import views.monitor     # noqa: F401
import views.library     # noqa: F401
from core.state import ctl

if __name__ in {'__main__', '__mp_main__'}:
    ui.run(host=os.environ.get('SC_HOST', '127.0.0.1'), port=int(os.environ.get('SC_PORT', '8080')),
           title='Stealth Cloner Pro', dark=True, reload=False, show=True,
           reconnect_timeout=600, favicon='🌐')
