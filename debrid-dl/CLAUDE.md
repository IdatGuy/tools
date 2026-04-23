# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`debrid-dl` is a single-file CLI tool (`debrid-dl.py`) that chains three services:
1. **Jackett** (self-hosted) — searches torrent indexers
2. **Real-Debrid** — resolves magnets into direct download links
3. **Direct HTTP download** — streams files with a progress bar

## Running

```bash
pip install requests
python debrid-dl.py "search query"
python debrid-dl.py "my show" --limit 10
python debrid-dl.py "my show" --download-dir /mnt/media/tv   # skips dir prompt
```

## Session workflow

The tool runs in a session loop:
1. Pick destination directory once (movies / tv / anime) — skipped if `--download-dir` is set
2. Search Jackett, pick one result
3. If the torrent has multiple files, pick which ones to download
4. Queue is shown; loop returns to search prompt automatically — enter `d` to download, `q` to quit
5. On confirm: all queued torrents are resolved and downloaded concurrently

## Configuration

Copy `config.example.json` to `config.json` and fill in:
- `jackett_url` — defaults to `http://localhost:9117`
- `jackett_api_key` — from Jackett's web UI
- `realdebrid_api_token` — from https://real-debrid.com/apitoken
- `download_dirs` — dict of named destinations (e.g. `{"movies": "/mnt/media/movies", "tv": "..."}`)
- `default_limit` — how many search results to show (default: 5)

`config.json` is gitignored (contains secrets). `config.example.json` is the template.

Old configs using `download_dir` (string) are automatically migrated to `download_dirs` at runtime.

## Architecture

Everything lives in `debrid-dl.py`. Key sections:

- `load_config()` — reads `config.json`, migrates old schema, validates
- `pick_download_dir()` — prompts for named dir or returns CLI override
- `search_jackett()` — calls Jackett's `/api/v2.0/indexers/all/results`, sorts by seeders
- `display_results()` — interactive table, returns single 0-based index
- `parse_selection()` — parses `1`, `1,3`, `1-3`, `1,3-5`, `all` into index lists
- `pick_files()` — shows per-file sizes, maps display indices → RD file IDs
- `RealDebrid` class:
  - `add_magnet()` → torrent ID
  - `wait_for_metadata()` — polls until file list is available (`waiting_files_selection`)
  - `select_files()` — accepts `"all"` or list of RD file IDs
  - `wait_for_links()` — polls until `downloaded`, thread-safe output via `threading.Lock`
  - `unrestrict()` — converts RD link to direct download URL
- `download_file()` — single file with `\r` progress bar
- `download_concurrent()` — `ThreadPoolExecutor`, labeled status lines per file
- `main()` — session loop + concurrent resolution + dispatch to single/multi download
