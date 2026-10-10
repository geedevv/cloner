from nicegui import ui

from core.config import CloneConfig, PRESET_LABELS, save_config
from core.state import ctl
from views.theme import shell


def _num(cfg, attr, label, lo=0, hi=None, step=1, hint=''):
    with ui.column().classes('gap-0 w-full'):
        ui.number(label, min=lo, max=hi, step=step, format='%.0f').bind_value(cfg, attr) \
            .props('outlined dense').classes('w-full')
        if hint:
            ui.label(hint).classes('field-hint')


def _sw(cfg, attr, label, hint=''):
    with ui.column().classes('gap-0 w-full'):
        ui.switch(label).bind_value(cfg, attr)
        if hint:
            ui.label(hint).classes('field-hint ml-12 -mt-1')


def _grid():
    return ui.grid().classes('w-full gap-x-4 gap-y-3 grid-cols-1 md:grid-cols-2 xl:grid-cols-3')


@ui.page('/settings')
def settings_page():
    cfg = ctl.cfg
    with shell('Settings'):
        with ui.row().classes('w-full items-center justify-between gap-2 flex-wrap'):
            ui.label('Settings').classes('text-lg font-bold')
            with ui.row().classes('gap-2 items-center'):
                def on_preset(e):
                    if e.value:
                        cfg.apply_preset(e.value)
                        ui.notify(f"Preset applied: {PRESET_LABELS[e.value]}")
                ui.select(PRESET_LABELS, value=cfg.preset, label='Preset', on_change=on_preset) \
                    .props('outlined dense').classes('w-72')

                def save():
                    save_config(cfg.normalize())
                    ui.notify('Settings saved.', type='positive')

                def reset():
                    cfg.reset_defaults()
                    ui.notify('Defaults restored (not saved yet).')
                ui.button('Save', icon='save', on_click=save).props('color=positive unelevated')
                ui.button('Defaults', icon='restart_alt', on_click=reset).props('outline')

        with ui.column().classes('card p-2 w-full'):
            with ui.tabs().classes('w-full').props('dense align=left active-color=cyan') as tabs:
                t = {k: ui.tab(k) for k in ('Limits', 'Flow control', 'Scan rules', 'Content',
                                            'Rendering', 'Browser ID')}
            with ui.tab_panels(tabs, value=t['Limits']).classes('w-full bg-transparent'):
                # -------------------------------------------------- limits
                with ui.tab_panel(t['Limits']):
                    with _grid():
                        _num(cfg, 'max_depth', 'Maximum mirroring depth', 0, 30,
                             hint='0 = only the start page (+ everything it needs)')
                        _num(cfg, 'external_depth', 'Maximum external depth', 0, 5,
                             hint='Only used when "Fetch other sites" is on')
                        _num(cfg, 'max_pages', 'Maximum pages', 1, 100000)
                        _num(cfg, 'max_assets', 'Maximum files', 10, 1000000)
                        _num(cfg, 'max_file_mb', 'Max size of any file (MB)', 1, 20000)
                        _num(cfg, 'max_total_mb', 'Site size limit (MB)', 10, 500000)
                        _num(cfg, 'max_time_min', 'Max time overall (minutes)', 0, 10000,
                             hint='0 = no time limit — the run stops itself when reached')
                        _num(cfg, 'max_query_variants', 'Query variants per page', 1, 100,
                             hint='Stops endless /page?x=1,2,3… crawls')
                        _num(cfg, 'max_path_depth', 'Max URL path depth', 2, 60,
                             hint='Stops crawler traps like /a/a/a/a/')
                # -------------------------------------------------- flow
                with ui.tab_panel(t['Flow control']):
                    with _grid():
                        _num(cfg, 'concurrency', 'Parallel jobs', 1, 64)
                        _num(cfg, 'per_host_conn', 'Connections per host', 1, 32)
                        _num(cfg, 'js_workers', 'Browser tabs (JS rendering)', 1, 8)
                        _num(cfg, 'retries', 'Retries per file', 1, 8)
                        _num(cfg, 'request_delay_ms', 'Delay between requests (ms)', 0, 60000,
                             hint='Random ±30% jitter. Be polite to small servers.')
                        _num(cfg, 'page_timeout_s', 'Page timeout (s)', 10, 600)
                        _num(cfg, 'asset_timeout_s', 'File timeout (s)', 5, 600)
                        _num(cfg, 'media_timeout_s', 'Video / big file timeout (s)', 30, 7200)
                        _num(cfg, 'stall_timeout_s', 'Stall watchdog (s)', 0, 3600,
                             hint='Stops the crawl if nothing progresses for this long (0 = off)')
                # -------------------------------------------------- scan rules
                with ui.tab_panel(t['Scan rules']):
                    ui.label('Use wildcards to include (+) or exclude (-) URLs. Last matching rule wins. '
                             'Example: +*.mp4 -*ads.example.com*').classes('text-sm text-slate-300')
                    ui.textarea('Scan rules').bind_value(cfg, 'scan_rules') \
                        .props('outlined rows=4').classes('w-full font-mono')
                    with _grid():
                        _sw(cfg, 'respect_robots', 'Respect robots.txt (pages)',
                            'Files a page needs to display are always fetched')
                        _sw(cfg, 'use_sitemap', 'Discover pages via sitemap.xml')
                        _sw(cfg, 'fetch_linked_files', 'Phase 2: linked files on this site',
                            'PDF / DOC / ZIP … fetched after the whole site is done')
                        _sw(cfg, 'fetch_external', 'Phase 3: fetch other sites',
                            'Off by default — runs only after everything else')
                        _sw(cfg, 'get_images', 'Images')
                        _sw(cfg, 'get_video', 'Video')
                        _sw(cfg, 'get_audio', 'Audio')
                        _sw(cfg, 'get_documents', 'Documents (pdf, doc, xls…)')
                        _sw(cfg, 'get_archives', 'Archives (zip, rar, 7z…)')
                # -------------------------------------------------- content
                with ui.tab_panel(t['Content']):
                    with _grid():
                        _sw(cfg, 'detect_hidden_links', 'Detect all links',
                            'Finds URLs inside scripts, JSON and data-* attributes (lazy images, sliders, video configs)')
                        _sw(cfg, 'inline_svg_sprites', 'Inline SVG sprites',
                            'Makes <use href="sprite.svg#x"> icons work on file://')
                        _sw(cfg, 'fetch_hls', 'Download HLS (.m3u8) video segments')
                        _sw(cfg, 'capture_api', 'Embed API / JSON responses',
                            'Lets fetch()/XHR data (animations, feeds) load on file://')
                        _sw(cfg, 'inline_fonts', 'Inline fonts into CSS',
                            'Fixes icon fonts on file://')
                        _sw(cfg, 'verify_pass', 'Verify & repair at the end',
                            'Re-checks every local link and re-downloads anything missing')
                        _num(cfg, 'repair_rounds', 'Repair rounds', 0, 10)
                # -------------------------------------------------- rendering
                with ui.tab_panel(t['Rendering']):
                    with _grid():
                        _sw(cfg, 'js_render', 'JavaScript rendering (Chromium)',
                            'Needed for SPAs, lazy content, sliders. Requires: playwright install chromium')
                        _sw(cfg, 'strip_scripts', 'Snapshot mode — remove scripts',
                            'Static copy of the rendered page. Most reliable on file://')
                        _sw(cfg, 'scroll_pass', 'Scroll through each page',
                            'Triggers lazy-loaded images, videos and animations')
                        _num(cfg, 'post_load_ms', 'Extra wait after load (ms)', 0, 60000)
                        _num(cfg, 'viewport_w', 'Viewport width', 320, 3840)
                        _num(cfg, 'viewport_h', 'Viewport height', 240, 2400)
                # -------------------------------------------------- identity
                with ui.tab_panel(t['Browser ID']):
                    with _grid():
                        ui.select(['chrome124', 'chrome120', 'chrome116', 'safari17_0', 'edge101'],
                                  label='Browser fingerprint (HTTP client)') \
                            .bind_value(cfg, 'impersonate').props('outlined dense')
                        ui.input('Custom User-Agent (overrides fingerprint)') \
                            .bind_value(cfg, 'custom_user_agent').props('outlined dense')
                        ui.input('Accept-Language').bind_value(cfg, 'accept_language').props('outlined dense')
                        ui.input('Default referer URL').bind_value(cfg, 'default_referer').props('outlined dense')
                        ui.input('Proxy (http://host:port)').bind_value(cfg, 'proxy').props('outlined dense')
                    ui.textarea('Additional HTTP headers (one per line: Name: value)') \
                        .bind_value(cfg, 'extra_headers').props('outlined rows=3').classes('w-full font-mono')
