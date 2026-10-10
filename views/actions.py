"""UI-level actions shared by several pages."""
import asyncio
from pathlib import Path

from nicegui import ui

from core import server as srv
from core import postproc
from core.state import ctl


def site_server():
    if ctl.server is None:
        ctl.server = srv.SiteServer()
    return ctl.server


def current_project() -> Path:
    return ctl.cfg.project_dir()


def act_open_file(project: Path = None):
    project = project or current_project()
    try:
        idx = srv.open_file_version(project)
        ui.notify(f"Opened {idx}" if idx else 'Nothing cloned yet — index.html not found.',
                  type='positive' if idx else 'warning')
    except Exception as e:
        ui.notify(f"Could not open: {e}", type='negative')


def act_open_http(project: Path = None):
    project = project or current_project()
    try:
        url = srv.open_http_version(site_server(), project)
        ui.notify(f"Serving at {url}" if url else 'Nothing cloned yet — index.html not found.',
                  type='positive' if url else 'warning')
    except Exception as e:
        ui.notify(f"Could not start server: {e}", type='negative')


def act_open_folder(project: Path = None):
    project = project or current_project()
    if not project.exists():
        ui.notify('Folder does not exist yet.', type='warning')
        return
    try:
        srv.open_folder(project)
    except Exception as e:
        ui.notify(f"{e}  ({project})", type='warning')


async def act_static_copy(project: Path = None):
    project = project or current_project()
    if not project.exists():
        ui.notify('Nothing to convert yet.', type='warning')
        return
    dst = project.with_name(project.name + '_static')
    ui.notify('Creating static (file://-safe) copy…')
    try:
        n = await asyncio.to_thread(postproc.make_static_copy, project, dst)
        ctl.log(f"Static copy created: {dst} ({n} pages)", 'OK')
        ui.notify(f"Static copy ready: {dst}", type='positive', timeout=8000)
    except Exception as e:
        ui.notify(f"Static copy failed: {e}", type='negative')


async def act_zip(project: Path = None):
    project = project or current_project()
    if not project.exists():
        ui.notify('Nothing to zip yet.', type='warning')
        return
    ui.notify('Zipping…')
    try:
        z = await asyncio.to_thread(postproc.zip_project, project)
        ui.download(str(z))
        ui.notify(f"Created {z}", type='positive')
    except Exception as e:
        ui.notify(f"Zip failed: {e}", type='negative')
