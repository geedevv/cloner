"""Shared look & feel + page shell (header, side navigation)."""
import html as html_lib
import json
import uuid
from contextlib import contextmanager

from nicegui import ui

from core.state import ctl

CSS = """
<style>
  html, body { background:#070b14; color:#e2e8f0; font-family:'Inter',system-ui,sans-serif; }
  .q-drawer { background:#0a101d !important; border-right:1px solid #1e293b; }
  .glass-header { background:rgba(9,14,26,.9); backdrop-filter:blur(12px); border-bottom:1px solid #1e293b; }
  .card { background:rgba(15,23,42,.7); border:1px solid #1e293b; border-radius:14px; }
  .card-title { font-size:11px; font-weight:700; letter-spacing:.14em; color:#94a3b8; text-transform:uppercase; }
  .stat-value { font-family:'JetBrains Mono',ui-monospace,monospace; }
  .nav-link { display:flex; align-items:center; gap:12px; padding:10px 14px; border-radius:10px;
              color:#94a3b8; text-decoration:none; font-weight:600; font-size:14px; }
  .nav-link:hover { background:#111a2e; color:#e2e8f0; }
  .nav-active { background:rgba(8,145,178,.15); color:#22d3ee !important; border:1px solid rgba(34,211,238,.25); }
  .pill { padding:4px 12px; border-radius:9999px; font-weight:700; font-size:11px; letter-spacing:.08em; border:1px solid; }
  .pill-idle { color:#94a3b8; background:rgba(148,163,184,.1); border-color:rgba(148,163,184,.35); }
  .pill-run  { color:#4ade80; background:rgba(34,197,94,.12); border-color:rgba(34,197,94,.45); }
  .pill-pause{ color:#fbbf24; background:rgba(245,158,11,.12); border-color:rgba(245,158,11,.45); }
  .phase { padding:6px 14px; border-radius:9999px; font-size:12px; font-weight:700; border:1px solid #1e293b; color:#475569; }
  .phase-on { color:#22d3ee; border-color:#22d3ee; background:rgba(34,211,238,.1); }
  .phase-done { color:#4ade80; border-color:rgba(74,222,128,.4); }
  .log-box { background:#050810; border:1px solid #1e293b; border-radius:12px; padding:10px 12px;
             font-family:'JetBrains Mono',ui-monospace,Consolas,monospace; font-size:12px; line-height:1.5;
             overflow-y:auto; width:100%; }
  .ll { white-space:pre-wrap; word-break:break-word; padding:0 2px; }
  .ll .t { color:#475569; } .ll .c { color:#64748b; font-weight:600; }
  .l-INFO{color:#cbe4f9} .l-OK{color:#bbf7d0} .l-PENDING{color:#fef08a} .l-WARN{color:#fed7aa}
  .l-ERROR{color:#fecaca;background:rgba(220,38,38,.1)}
  .field-hint { font-size:11px; color:#64748b; }
</style>
"""

NAV = [('Dashboard', 'space_dashboard', '/'), ('Settings', 'tune', '/settings'),
       ('Monitor', 'terminal', '/monitor'), ('Library', 'folder_special', '/library')]


@contextmanager
def shell(active: str):
    ui.dark_mode().enable()
    ui.add_head_html(CSS)
    with ui.header().classes('glass-header items-center justify-between px-4 py-2'):
        with ui.row().classes('items-center gap-3'):
            ui.button(icon='menu', on_click=lambda: drawer.toggle()).props('flat round dense color=cyan')
            ui.icon('travel_explore').classes('text-2xl text-cyan-400')
            ui.label('STEALTH CLONER PRO').classes('text-base font-bold tracking-widest')
            ui.label('v9').classes('text-xs font-mono text-cyan-400 px-2 rounded border border-cyan-500/30')
        with ui.row().classes('items-center gap-3'):
            elapsed = ui.label('').classes('text-xs font-mono text-slate-500')
            pill = ui.label('IDLE').classes('pill pill-idle')

    with ui.left_drawer(value=True, fixed=True).props('width=220 breakpoint=900') as drawer:
        ui.label('MENU').classes('card-title px-3 pt-2 pb-2')
        for label, icon, href in NAV:
            with ui.link(target=href).classes('nav-link' + (' nav-active' if label == active else '')):
                ui.icon(icon).classes('text-lg')
                ui.label(label)
        ui.separator().classes('my-3 bg-slate-800')
        ui.label('Only clone sites you own or have permission to mirror.').classes(
            'text-[11px] text-slate-600 px-3 leading-snug')

    def refresh():
        ctl.tick()
        pill.set_text(ctl.status_text)
        pill.classes(replace='pill ' + ('pill-idle' if not ctl.is_running else
                                        'pill-pause' if ctl.is_paused else 'pill-run'))
        elapsed.set_text(f"elapsed {ctl.stats.elapsed}" if ctl.is_running else '')
    ui.timer(0.5, refresh)

    with ui.column().classes('w-full gap-4 p-4 max-w-[1600px] mx-auto'):
        yield


class LogView:
    """Incremental colour log; each page instance keeps its own read cursor."""

    def __init__(self, height='360px', max_lines=1500):
        self.cls = 'lv-' + uuid.uuid4().hex[:8]
        self.idx = 0
        self.level = 'ALL'
        self.text = ''
        self.max_lines = max_lines
        ui.element('div').classes(f'log-box {self.cls}').style(f'height:{height};')
        ui.timer(0.4, self.pump)

    def _match(self, e):
        if self.level != 'ALL' and e['level'] != self.level:
            return False
        return not self.text or self.text in e['msg'].lower()

    def _js(self, body):
        ui.run_javascript(
            f"(function(){{var el=document.querySelector('.{self.cls}');if(!el)return;{body}}})();")

    def clear_dom(self):
        self._js("el.innerHTML='';")

    def set_filter(self, level=None, text=None):
        if level is not None:
            self.level = level
        if text is not None:
            self.text = text.strip().lower()
        self.idx = 0
        self.clear_dom()
        self.pump()

    def pump(self):
        entries = ctl.log_entries
        if self.idx > len(entries):          # log was reset
            self.idx = 0
            self.clear_dom()
        if self.idx >= len(entries):
            return
        new = entries[self.idx:]
        self.idx = len(entries)
        chunks = [f'<div class="ll l-{e["level"]}"><span class="t">[{e["ts"]}]</span> '
                  f'<span class="c">[{e["category"]}]</span> {html_lib.escape(e["msg"])}</div>'
                  for e in new if self._match(e)]
        if not chunks:
            return
        payload = json.dumps(''.join(chunks))
        self._js(f"var stick=el.scrollTop+el.clientHeight>=el.scrollHeight-40;"
                 f"el.insertAdjacentHTML('beforeend',{payload});"
                 f"while(el.childElementCount>{self.max_lines})el.removeChild(el.firstChild);"
                 f"if(stick)el.scrollTop=el.scrollHeight;")
