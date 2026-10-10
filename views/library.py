import shutil
from pathlib import Path

from nicegui import ui

from core import postproc
from core.state import ctl
from core.urlutil import human_size
from views import actions
from views.theme import shell


@ui.page('/library')
def library_page():
    with shell('Library'):
        with ui.row().classes('w-full items-center justify-between'):
            ui.label('Your clones').classes('text-lg font-bold')
            ui.button('Refresh', icon='refresh', on_click=lambda: build()).props('outline dense')
        ui.label('').bind_text_from(ctl.cfg, 'output_dir', backward=lambda v: f'Scanning: {v}') \
            .classes('field-hint')
        box = ui.column().classes('w-full gap-3')

        def confirm_delete(p: Path):
            with ui.dialog() as d, ui.card().classes('card p-5'):
                ui.label(f"Delete '{p.name}' and all its files?").classes('font-bold')
                with ui.row().classes('justify-end w-full gap-2'):
                    ui.button('Cancel', on_click=d.close).props('flat')

                    def go():
                        shutil.rmtree(p, ignore_errors=True)
                        d.close()
                        build()
                    ui.button('Delete', on_click=go).props('color=negative')
            d.open()

        def build():
            box.clear()
            projects = postproc.list_projects(Path(ctl.cfg.output_dir))
            with box:
                if not projects:
                    ui.label('No clones yet — run one from the Dashboard.').classes('text-slate-500')
                for pr in projects:
                    m, path = pr['manifest'], pr['path']
                    audit = m.get('audit') or {}
                    with ui.column().classes('card p-4 gap-2 w-full'):
                        with ui.row().classes('items-center justify-between w-full flex-wrap'):
                            with ui.column().classes('gap-0'):
                                ui.label(pr['name']).classes('text-base font-bold')
                                ui.label(m.get('source', '')).classes('text-xs text-slate-400 font-mono')
                            with ui.row().classes('gap-4 text-xs font-mono text-slate-300'):
                                ui.label(f"{m.get('pages', 0)} pages")
                                ui.label(f"{m.get('assets', 0)} files")
                                ui.label(human_size(pr['size']))
                                ui.label(m.get('created', '')[:16].replace('T', ' '))
                        info = m.get('mode', '')
                        if audit:
                            info += (f"  ·  links fixed: {audit.get('repaired', 0)}  ·  "
                                     f"kept online: {audit.get('reverted_to_online_links', 0)}")
                        ui.label(info).classes('field-hint')
                        with ui.row().classes('gap-2 flex-wrap'):
                            ui.button('file://', icon='folder_open',
                                      on_click=lambda p=path: actions.act_open_file(p)) \
                                .props('color=cyan unelevated dense')
                            ui.button('http://', icon='language',
                                      on_click=lambda p=path: actions.act_open_http(p)) \
                                .props('color=indigo unelevated dense')
                            ui.button('Folder', icon='drive_folder_upload',
                                      on_click=lambda p=path: actions.act_open_folder(p)).props('outline dense')
                            ui.button('Static copy', icon='layers',
                                      on_click=lambda p=path: actions.act_static_copy(p)).props('outline dense')
                            ui.button('ZIP', icon='archive',
                                      on_click=lambda p=path: actions.act_zip(p)).props('outline dense')
                            ui.button('Delete', icon='delete',
                                      on_click=lambda p=path: confirm_delete(p)).props('flat dense color=negative')
        build()
