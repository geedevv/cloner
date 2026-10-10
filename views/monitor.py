from nicegui import ui

from core.state import ctl
from views.theme import LogView, shell


@ui.page('/monitor')
def monitor_page():
    with shell('Monitor'):
        with ui.column().classes('card p-4 gap-3 w-full'):
            with ui.row().classes('items-center gap-2 flex-wrap w-full'):
                ui.label('Operation log').classes('card-title')
                ui.space()
                holder = {}
                for lvl in ('ALL', 'INFO', 'OK', 'PENDING', 'WARN', 'ERROR'):
                    ui.button(lvl, on_click=lambda l=lvl: holder['lv'].set_filter(level=l)) \
                        .props('flat dense size=sm color=cyan')
                ui.input(placeholder='search…', on_change=lambda e: holder['lv'].set_filter(text=e.value)) \
                    .props('outlined dense').classes('w-48')
            holder['lv'] = LogView(height='calc(100vh - 470px)')
            with ui.row().classes('gap-2'):
                def dl_log():
                    if ctl.log_file_path and ctl.log_file_path.exists():
                        ui.download(str(ctl.log_file_path))
                    else:
                        ui.notify('No log file yet.', type='warning')
                ui.button('Download log', icon='download', on_click=dl_log).props('outline dense')
                ui.label('').bind_text_from(ctl, 'log_file_path', backward=lambda p: str(p or '')) \
                    .classes('field-hint')

        with ui.row().classes('w-full gap-4 flex-col lg:flex-row items-stretch'):
            with ui.column().classes('card p-4 gap-2 flex-1 min-w-0'):
                ui.label('Recent files').classes('card-title')
                files_box = ui.column().classes('w-full gap-0 h-56 overflow-y-auto')
            with ui.column().classes('card p-4 gap-2 flex-1 min-w-0'):
                ui.label('Failed downloads').classes('card-title')
                fail_box = ui.column().classes('w-full gap-0 h-56 overflow-y-auto')

        snap = {'f': -1, 'e': -1}

        def refresh():
            if snap['f'] != len(ctl.recent_files):
                snap['f'] = len(ctl.recent_files)
                files_box.clear()
                with files_box:
                    for f in reversed(ctl.recent_files[-60:]):
                        with ui.row().classes('w-full justify-between text-[11px] font-mono'):
                            ui.label(f['name']).classes('truncate text-slate-300')
                            ui.label(f"{f['size']:,} B").classes('text-slate-500')
            if snap['e'] != len(ctl.failures):
                snap['e'] = len(ctl.failures)
                fail_box.clear()
                with fail_box:
                    if not ctl.failures:
                        ui.label('None 🎉').classes('text-slate-500 text-xs')
                    for f in reversed(ctl.failures[-80:]):
                        ui.label(f"{f['reason']} — {f['url']}").classes(
                            'text-[11px] font-mono text-red-300 break-all')
        ui.timer(0.8, refresh)
