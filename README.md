# Stealth Cloner Pro v9

Website archiver with a multi-page UI (Dashboard · Settings · Monitor · Library).

## Install & run
```
pip install -r requirements.txt
playwright install chromium
python main.py          # opens http://127.0.0.1:8080
```

## How it clones (priority order)
1. **Core site** – every page + everything each page needs to render (images, video, icons, fonts,
   CSS, JS, animation JSON, SVG sprites), from any host/CDN.
2. **Linked files** – same-site PDFs/docs (optional).
3. **External** – other sites (off by default, runs last).
4. **Verify & repair** – re-checks every local link, re-downloads missing files, and points anything
   still missing back to the original online URL (no broken images while online).

## Opening a clone
* **file:// version** – double-click / button. Works for classic scripts, images, fonts, video, CSS,
  fetch/XHR data (embedded), JS-assigned image/video/script URLs (shim).
* **http:// version** – built-in server with Range support (video seeking). Also `serve.py` in every clone.
* **Static copy** – removes all scripts; guaranteed to open from file:// (use for `type="module"` sites).

## Endless-crawl protection
depth / page / file / size / time limits, query-variant cap, path-loop detection, stall watchdog.
