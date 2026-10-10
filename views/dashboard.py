from pathlib import Path

from nicegui import ui

from core.config import PRESET_LABELS, save_config
from core.server import find_index
from core.state import PHASES, ctl
from core.urlutil import human_size
from views import actions
from views.theme import LogView, shell


@ui.page('/')
def dashboard_page():
    cfg, st = ctl.cfg, ctl.stats
    with shell('Dashboard'):
        with ui.row().classes('w-full gap-4 items-stretch flex-col lg:flex-row'):
            # ------------------------------------------------------------ target
            with ui.column().classes('card p-5 gap-3 w-full lg:w-[400px] shrink-0'):
                ui.label('Target').classes('card-title')
                ui.input('Website URL', placeholder='https://example.com').bind_value(cfg, 'url') \
                    .classes('w-full').props('outlined dense')
                ui.input('Project name').bind_value(cfg, 'project_name') \
                    .classes('w-full').props('outlined dense')
                ui.input('Output folder').bind_value(cfg, 'output_dir') \
                    .classes('w-full').props('outlined dense')

                def on_preset(e):
                    if e.value:
                        cfg.apply_preset(e.value)
                        ui.notify(f"Preset applied: {PRESET_LABELS[e.value]}")
                ui.select(PRESET_LABELS, value=cfg.preset, label='Preset', on_change=on_preset) \
                    .classes('w-full').props('outlined dense')
                ui.label('Fine-tune everything in Settings.').classes('field-hint')

                with ui.row().classes('w-full gap-2 mt-1'):
                    start_btn = ui.button('START CLONE', icon='play_arrow').classes('flex-grow') \
                        .props('color=positive unelevated')
                    pause_btn = ui.button(icon='pause').props('color=warning unelevated')
                    stop_btn = ui.button(icon='stop').props('color=negative unelevated')

                async def do_start():
                    save_config(cfg.normalize())
                    ok, msg = await ctl.start()
                    ui.notify(msg, type='positive' if ok else 'warning')
                start_btn.on_click(do_start)
                pause_btn.on_click(ctl.pause_toggle)
                stop_btn.on_click(ctl.stop)

                ui.separator().classes('bg-slate-800')
                ui.label('Open the clone').classes('card-title')
                ui.label('Available as soon as the first page is saved — even while cloning.') \
                    .classes('field-hint')
                with ui.row().classes('w-full gap-2'):
                    b_file = ui.button('file:// version', icon='folder_open',
                                       on_click=lambda: actions.act_open_file()) \
                        .classes('flex-grow').props('color=cyan unelevated')
                    b_http = ui.button('http:// version', icon='language',
                                       on_click=lambda: actions.act_open_http()) \
                        .classes('flex-grow').props('color=indigo unelevated')
                with ui.row().classes('w-full gap-2'):
                    ui.button('Folder', icon='drive_folder_upload',
                              on_click=lambda: actions.act_open_folder()).props('outline dense')
                    ui.button('Static copy', icon='layers',
                              on_click=actions.act_static_copy).props('outline dense') \
                        .tooltip('Scripts removed — guaranteed to open from file://')
                    ui.button('ZIP', icon='archive', on_click=actions.act_zip).props('outline dense')
                ready_label = ui.label('').classes('field-hint')

            # ------------------------------------------------------------ live
            with ui.column().classes('flex-1 min-w-0 gap-4'):
                with ui.column().classes('card p-4 gap-3 w-full'):
                    ui.label('Pipeline').classes('card-title')
                    with ui.row().classes('gap-2 flex-wrap'):
                        chips = {k: ui.label(lbl).classes('phase') for k, lbl in PHASES}
                    prog = ui.linear_progress(value=0, show_value=False).props('color=cyan rounded size=8px')
                    cur = ui.label('').classes('text-xs font-mono text-slate-400 truncate w-full')

                with ui.row().classes('w-full gap-3 flex-wrap'):
                    def stat(title, icon, color, attr, fmt=str):
                        with ui.column().classes('card p-3 gap-0 flex-1 min-w-[130px]'):
                            with ui.row().classes('items-center gap-2'):
                                ui.icon(icon).classes(f'text-lg text-{color}-400')
                                ui.label(title).classes('card-title')
                            ui.label('0').classes(f'stat-value text-2xl font-bold text-{color}-400') \
                                .bind_text_from(st, attr, backward=fmt)
                    stat('Pages', 'article', 'green', 'pages')
                    stat('Files', 'inventory_2', 'blue', 'assets')
                    stat('Queued', 'hourglass_top', 'yellow', 'queued')
                    stat('Size', 'data_usage', 'purple', 'total_bytes', human_size)
                    stat('Errors', 'error_outline', 'red', 'errors')
                    stat('Embedded', 'data_object', 'cyan', 'embedded')

                with ui.column().classes('card p-4 gap-2 w-full'):
                    with ui.row().classes('items-center justify-between w-full'):
                        ui.label('Live log').classes('card-title')
                        ui.link('Open full monitor →', '/monitor').classes('text-xs text-cyan-400')
                    LogView(height='340px')

        def refresh():
            running = ctl.is_running
            start_btn.set_enabled(not running)
            pause_btn.set_enabled(running)
            stop_btn.set_enabled(running)
            start_btn.set_text('CLONING…' if running else 'START CLONE')
            order = [k for k, _ in PHASES]
            cur_i = order.index(st.phase) if st.phase in order else -1
            for i, (k, _) in enumerate(PHASES):
                cls = 'phase'
                if running and i == cur_i:
                    cls += ' phase-on'
                elif (running and i < cur_i) or (not running and ctl.last_result and st.pages):
                    cls += ' phase-done'
                chips[k].classes(replace=cls)
            tot = st.done + st.queued
            prog.set_value((st.done / tot) if (running and tot) else (1 if ctl.last_result and not running else 0))
            cur.set_text(st.current_url if running else (ctl.last_result or ''))
            proj = cfg.project_dir()
            idx = find_index(proj) if proj.exists() else None
            for b in (b_file, b_http):
                b.set_enabled(bool(idx))
            ready_label.set_text(f"Ready: {idx}" if idx else 'No index.html yet.')
        ui.timer(0.6, refresh)
