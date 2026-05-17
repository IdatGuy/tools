#!/usr/bin/env python3
"""
debrid-dl: Search torrents via Jackett, resolve via Real-Debrid, download directly.

Usage:
    python debrid-dl.py "search query"
    python debrid-dl.py "my show" --limit 10
    python debrid-dl.py "my show" --download-dir /mnt/media/tv

Workflow:
    Pick directory → search → pick torrent → pick files → search again or download all

Setup:
    1. Install deps:  pip install requests
    2. Copy config:   cp config.example.json config.json
    3. Fill in your Jackett API key, Real-Debrid API token, and download directories.

    Jackett: Self-host via Docker — handles searching across torrent indexers.
    Real-Debrid API token: https://real-debrid.com/apitoken
"""

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

BANNER = """\
  ██████╗ ███████╗██████╗ ██████╗ ██╗██████╗     ██████╗ ██╗
  ██╔══██╗██╔════╝██╔══██╗██╔══██╗██║██╔══██╗    ██╔══██╗██║
  ██║  ██║█████╗  ██████╔╝██████╔╝██║██║  ██║    ██║  ██║██║
  ██║  ██║██╔══╝  ██╔══██╗██╔══██╗██║██║  ██║    ██║  ██║██║
  ██████╔╝███████╗██████╔╝██║  ██║██║██████╔╝    ██████╔╝███████╗
  ╚═════╝ ╚══════╝╚═════╝ ╚═╝  ╚═╝╚═╝╚═════╝     ╚═════╝ ╚══════╝
            torrent  →  Real-Debrid  →  direct download
"""

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).parent / "config.json"

DEFAULT_CONFIG = {
    "jackett_url": "http://localhost:9117",
    "jackett_api_key": "YOUR_JACKETT_API_KEY",
    "realdebrid_api_token": "YOUR_REALDEBRID_TOKEN",
    "download_dirs": {
        "movies": str(Path.home() / "Downloads" / "movies"),
        "tv": str(Path.home() / "Downloads" / "tv"),
        "anime": str(Path.home() / "Downloads" / "anime"),
    },
    "default_limit": 5,
    "max_concurrent_downloads": 3,
}


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"[!] No config found. Creating {CONFIG_PATH}")
        print("    Fill in your API keys and re-run.\n")
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        sys.exit(1)

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    # Migrate old download_dir → download_dirs
    if "download_dirs" not in cfg and "download_dir" in cfg:
        print("[i] Migrated 'download_dir' → 'download_dirs'. Update config.json to add more directories.")
        cfg["download_dirs"] = {"downloads": cfg.pop("download_dir")}

    # Validate required fields
    missing = []
    if cfg.get("jackett_api_key", "").startswith("YOUR_"):
        missing.append("jackett_api_key")
    if cfg.get("realdebrid_api_token", "").startswith("YOUR_"):
        missing.append("realdebrid_api_token")
    if not isinstance(cfg.get("download_dirs"), dict) or not cfg["download_dirs"]:
        missing.append("download_dirs")
    if missing:
        print(f"[!] Config incomplete — fill in: {', '.join(missing)}")
        print(f"    Edit: {CONFIG_PATH}")
        sys.exit(1)

    return cfg


# ---------------------------------------------------------------------------
# Directory selection
# ---------------------------------------------------------------------------

_SERIES_DIR_KEYS = {"tv", "anime"}


def _sanitize_dirname(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', '', name).strip()
    return re.sub(r'\s+', '_', name).lower()


def _prompt_series_subdir(base: str) -> str:
    """Prompt for show name and season, return subdirectory path."""
    show = ""
    while not show:
        show = input("  Show name: ").strip()
    show = _sanitize_dirname(show)

    season_raw = input("  Season [1]: ").strip()
    season = int(season_raw) if season_raw.isdigit() else 1

    return os.path.join(base, show, f"S{season:02d}")


def pick_download_dir(cfg: dict, cli_override: str | None) -> str:
    """Return download directory: CLI override, auto-select if only one, or prompt."""
    if cli_override:
        # Accept a named key with optional subdirectory (e.g. --download-dir tv  or  tv/showname/S01)
        dirs = cfg["download_dirs"]
        parts = cli_override.split("/", 1)
        if parts[0] in dirs:
            base = dirs[parts[0]]
            return os.path.join(base, parts[1]) if len(parts) > 1 else base
        return cli_override

    dirs = cfg["download_dirs"]
    if len(dirs) == 1:
        key = next(iter(dirs))
        base = dirs[key]
        if key in _SERIES_DIR_KEYS:
            return _prompt_series_subdir(base)
        return base

    names = list(dirs.keys())
    print("\nSave to:")
    for i, name in enumerate(names):
        print(f"  {i + 1}. {name:<10}  →  {dirs[name]}")

    while True:
        try:
            choice = input("Pick directory (number, or 'q' to quit): ").strip()
            if choice.lower() == "q":
                sys.exit(0)
            idx = int(choice) - 1
            if 0 <= idx < len(names):
                key = names[idx]
                base = dirs[key]
                if key in _SERIES_DIR_KEYS:
                    return _prompt_series_subdir(base)
                return base
            print(f"  Enter 1-{len(names)}")
        except (ValueError, EOFError):
            print(f"  Enter 1-{len(names)}")


# ---------------------------------------------------------------------------
# Jackett search
# ---------------------------------------------------------------------------

def search_jackett(cfg: dict, query: str, limit: int) -> list[dict]:
    """Search all configured Jackett indexers, return sorted results."""
    url = f"{cfg['jackett_url']}/api/v2.0/indexers/all/results"
    params = {
        "apikey": cfg["jackett_api_key"],
        "Query": query,
    }

    print(f"\n🔍 Searching for: {query}")
    try:
        resp = requests.get(url, params=params, timeout=90)
        resp.raise_for_status()
    except requests.ConnectionError:
        print(f"[!] Can't connect to Jackett at {cfg['jackett_url']}")
        print("    Make sure Jackett is running.")
        sys.exit(1)
    except requests.Timeout:
        print(f"[!] Jackett timed out at {cfg['jackett_url']}")
        print("    Jackett may be starting up or querying slow indexers — try again.")
        sys.exit(1)
    except requests.HTTPError as e:
        print(f"[!] Jackett error: {e}")
        sys.exit(1)

    data = resp.json()
    results = data.get("Results", [])

    if not results:
        print("No results found.")
        return []

    # Normalize and sort by seeders
    parsed = []
    for r in results:
        magnet = r.get("MagnetUri") or r.get("Link")
        if not magnet:
            continue
        parsed.append({
            "title": r.get("Title", "Unknown"),
            "size": r.get("Size", 0),
            "seeders": r.get("Seeders", 0),
            "peers": r.get("Peers", 0),
            "tracker": r.get("Tracker", "?"),
            "magnet": magnet,
        })

    parsed.sort(key=lambda x: x["seeders"], reverse=True)
    return parsed[:limit]


def format_size(size_bytes: int) -> str:
    """Human-readable file size."""
    if size_bytes <= 0:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def display_results(results: list[dict]) -> list[int] | None:
    """Show results table, return list of user's 0-based choices, or None to re-search."""
    print(f"\n{'#':<4} {'Size':<10} {'S/P':<10} {'Tracker':<15} Title")
    print("-" * 80)
    for i, r in enumerate(results):
        size = format_size(r["size"])
        sp = f"{r['seeders']}/{r['peers']}"
        tracker = r["tracker"][:14]
        title = r["title"][:60]
        print(f"{i + 1:<4} {size:<10} {sp:<10} {tracker:<15} {title}")

    print()
    while True:
        try:
            choice = input("Pick result(s) (e.g. 1  1,3  1-3  s=search  q): ").strip()
            if choice.lower() == "q":
                sys.exit(0)
            if choice.lower() == "s":
                return None
            indices = parse_selection(choice, len(results) - 1)
            if indices:
                return indices
            print(f"  Enter numbers 1-{len(results)}, ranges, or comma-separated")
        except EOFError:
            sys.exit(0)


# ---------------------------------------------------------------------------
# File selection
# ---------------------------------------------------------------------------

def parse_selection(raw: str, max_idx: int) -> list[int]:
    """
    Parse a selection string into a list of 0-based indices.
    Handles: single numbers, comma-separated, ranges (1-3), mixed (1,3-5).
    Returns empty list on invalid input.
    """
    raw = raw.strip()
    if not raw or raw.lower() == "q":
        return []
    if raw.lower() == "all":
        return list(range(max_idx + 1))

    indices = []
    for token in raw.split(","):
        token = token.strip()
        if "-" in token:
            parts = token.split("-", 1)
            try:
                lo, hi = int(parts[0]) - 1, int(parts[1]) - 1
            except ValueError:
                return []
            if lo > hi or lo < 0 or hi > max_idx:
                return []
            indices.extend(range(lo, hi + 1))
        else:
            try:
                idx = int(token) - 1
            except ValueError:
                return []
            if idx < 0 or idx > max_idx:
                return []
            indices.append(idx)

    return list(dict.fromkeys(indices))  # deduplicate, preserve order


def pick_files(files: list[dict]) -> list[int] | str:
    """
    Show torrent file list and prompt for selection.
    Returns list of RD file IDs or "all".
    """
    print("\nFiles in torrent:")
    for i, f in enumerate(files):
        size = format_size(f.get("bytes", 0))
        path = f.get("path", "?")
        print(f"  {i + 1:<4} [{size:>8}]  {path}")

    print()
    while True:
        try:
            raw = input("Pick files (e.g. 1  1,3  1-3  all  q): ").strip()
            if raw.lower() == "q":
                sys.exit(0)
            if raw.lower() == "all":
                return "all"
            indices = parse_selection(raw, len(files) - 1)
            if indices:
                return [files[i]["id"] for i in indices]
            print(f"  Invalid — enter numbers 1-{len(files)}, ranges, or 'all'")
        except EOFError:
            return "all"


# ---------------------------------------------------------------------------
# Real-Debrid
# ---------------------------------------------------------------------------

class RealDebrid:
    BASE = "https://api.real-debrid.com/rest/1.0"

    def __init__(self, token: str):
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {token}"

    def _post(self, endpoint: str, data: dict = None, retries: int = 3) -> dict | None:
        for attempt in range(retries):
            try:
                resp = self.session.post(f"{self.BASE}{endpoint}", data=data)
                resp.raise_for_status()
                if resp.status_code == 204 or not resp.text:
                    return None
                return resp.json()
            except requests.exceptions.ConnectionError:
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt)

    def _put(self, endpoint: str, content: bytes) -> dict | None:
        resp = self.session.put(f"{self.BASE}{endpoint}", data=content)
        resp.raise_for_status()
        if resp.status_code == 204 or not resp.text:
            return None
        return resp.json()

    def _get(self, endpoint: str) -> dict:
        resp = self.session.get(f"{self.BASE}{endpoint}")
        resp.raise_for_status()
        return resp.json()

    def add_magnet(self, magnet: str) -> str:
        """Add magnet or torrent URL, return torrent ID."""
        if magnet.startswith("magnet:"):
            result = self._post("/torrents/addMagnet", {"magnet": magnet})
        else:
            # Link may be a .torrent file or may redirect to a magnet URI.
            # Follow redirects manually so we can intercept magnet: hops.
            url = magnet
            resp = requests.get(url, allow_redirects=False, timeout=15)
            while resp.is_redirect:
                location = resp.headers.get("Location", "")
                if location.startswith("magnet:"):
                    result = self._post("/torrents/addMagnet", {"magnet": location})
                    return result["id"]
                resp = requests.get(location, allow_redirects=False, timeout=15)
            result = self._put("/torrents/addTorrent", resp.content)
        return result["id"]

    def wait_for_metadata(self, torrent_id: str, timeout: int = 30) -> dict:
        """Poll until RD has the file list ready. Returns full torrent info dict."""
        start = time.time()
        while time.time() - start < timeout:
            info = self._get(f"/torrents/info/{torrent_id}")
            status = info.get("status")
            if status in ("waiting_files_selection", "downloaded"):
                print()  # clear \r line
                return info
            if status in ("magnet_error", "error", "virus", "dead"):
                print(f"\n[!] Real-Debrid error: {status}")
                sys.exit(1)
            print(f"   Fetching torrent info... ({status})", end="\r")
            time.sleep(1)
        print()
        print("[!] Timed out waiting for torrent metadata")
        sys.exit(1)

    def select_files(self, torrent_id: str, file_ids: list[int] | str = "all"):
        """Select files to download: pass 'all' or a list of RD file IDs."""
        if file_ids == "all" or not file_ids:
            files_param = "all"
        else:
            files_param = ",".join(str(i) for i in file_ids)
        self._post(f"/torrents/selectFiles/{torrent_id}", {"files": files_param})

    def wait_for_links(
        self, torrent_id: str, label: str, lock: threading.Lock, timeout: int = 120
    ) -> list[str]:
        """Poll until download links are ready. Thread-safe output via lock."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                info = self._get(f"/torrents/info/{torrent_id}")
            except requests.ConnectionError:
                time.sleep(2)
                continue
            status = info.get("status")

            if status == "downloaded":
                links = info.get("links", [])
                if links:
                    with lock:
                        print(f"[{label}] ready ({len(links)} link{'s' if len(links) != 1 else ''})")
                    return links

            if status in ("magnet_error", "error", "virus", "dead"):
                with lock:
                    print(f"[{label}] error: {status}")
                sys.exit(1)

            progress = info.get("progress", 0)
            with lock:
                print(f"[{label}] {status} {progress}%")
            time.sleep(2)

        with lock:
            print(f"[{label}] timed out waiting for links")
        sys.exit(1)

    def unrestrict(self, link: str) -> dict:
        """Unrestrict a link to get direct download URL."""
        return self._post("/unrestrict/link", {"link": link})


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_file(url: str, dest_dir: str, filename: str = None, silent: bool = False, lock: threading.Lock = None):
    """Download a single file with a progress bar.

    silent=True suppresses the \r progress bar (used during concurrent downloads).
    lock is used for thread-safe prints when silent=True.
    Returns the Path on success, None if the download was skipped due to a timeout at 0%.
    """
    os.makedirs(dest_dir, exist_ok=True)

    try:
        resp = requests.get(url, stream=True, timeout=(15, 60))
    except requests.exceptions.Timeout:
        msg = f"[!] Skipped (timed out connecting): {filename or url}"
        if lock:
            with lock:
                print(msg)
        else:
            print(msg)
        return None
    resp.raise_for_status()

    if not filename:
        cd = resp.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            filename = cd.split("filename=")[-1].strip('"\'')
        else:
            filename = url.split("/")[-1].split("?")[0] or "download"

    filepath = Path(dest_dir) / filename
    total = int(resp.headers.get("Content-Length", 0))

    if silent:
        def log(msg):
            with lock:
                print(msg)
        log(f"[→] {filename}" + (f"  ({format_size(total)})" if total else ""))
    else:
        print(f"\n📥 Downloading: {filename}")
        print(f"   To: {filepath}")
        if total:
            print(f"   Size: {format_size(total)}")

    downloaded = 0
    chunk_size = 1024 * 1024  # 1MB

    try:
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                f.write(chunk)
                downloaded += len(chunk)
                if not silent and total:
                    pct = downloaded / total * 100
                    bar_len = 30
                    filled = int(bar_len * downloaded / total)
                    bar = "█" * filled + "░" * (bar_len - filled)
                    print(f"   [{bar}] {pct:5.1f}%  {format_size(downloaded)}", end="\r")
    except requests.exceptions.Timeout:
        if downloaded == 0:
            msg = f"[!] Skipped (timed out at 0%): {filename}"
        else:
            msg = f"[!] Skipped (timed out at {format_size(downloaded)} / {format_size(total) if total else '?'}): {filename}"
        if silent:
            log(msg)
        else:
            print(f"\n{msg}")
        return None

    if silent:
        log(f"[✓] {filename}  ({format_size(downloaded)})")
    else:
        print(f"\n\n✅ Done: {filepath}")
    return filepath


def download_concurrent(tasks: list[tuple], max_workers: int):
    """Download files concurrently. tasks = [(url, dest_dir, filename), ...]"""
    n = len(tasks)
    print(f"\n📥 Downloading {n} file{'s' if n != 1 else ''}...")

    if max_workers == 1:
        for i, (url, dest_dir, filename) in enumerate(tasks, 1):
            if n > 1:
                print(f"\n[{i}/{n}]")
            download_file(url, dest_dir, filename)
        return

    lock = threading.Lock()
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(download_file, url, dest_dir, filename, True, lock): filename
            for url, dest_dir, filename in tasks
        }
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                fname = futures[future]
                with lock:
                    print(f"[!] Error downloading {fname}: {e}")


# ---------------------------------------------------------------------------
# Queue display
# ---------------------------------------------------------------------------

def print_queue(queue: list[dict]):
    n = len(queue)
    print(f"\nQueue ({n} item{'s' if n != 1 else ''}):")
    for item in queue:
        files_note = f" ({item['file_count']} files)" if item["file_count"] > 1 else ""
        title = item["title"][:50]
        dest = item["dest_dir"]
        print(f"  • {title}{files_note}  →  {dest}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Search torrents → Real-Debrid → direct download"
    )
    parser.add_argument("query", nargs="?", help="Initial search query")
    parser.add_argument(
        "--limit", "-n", type=int, default=None,
        help="Number of results to show (default: from config)"
    )
    parser.add_argument(
        "--download-dir", "-d", default=None,
        help="Download destination override (skips directory prompt)"
    )
    args = parser.parse_args()

    print(BANNER)
    cfg = load_config()
    limit = args.limit or cfg.get("default_limit", 5)
    rd = RealDebrid(cfg["realdebrid_api_token"])

    queue = []  # list of {torrent_id, dest_dir, title, file_count}
    query = args.query

    # Pick directory once up front (skipped if CLI override provided)
    dest_dir = pick_download_dir(cfg, args.download_dir)

    first_iteration = True

    while True:
        # Prompt for query on subsequent loops or if none was provided
        if not first_iteration or not query:
            try:
                prompt = "\nSearch query (d to download, q to quit): "
                query = input(prompt).strip()
                if not query:
                    continue
                if query.lower() == "q":
                    sys.exit(0)
                if query.lower() == "d":
                    break
            except EOFError:
                break
        first_iteration = False

        # 1. Search Jackett
        results = search_jackett(cfg, query, limit)
        if not results:
            continue

        # 2. Pick result(s)
        choices = display_results(results)
        if choices is None:
            query = None
            first_iteration = False
            continue
        selected_results = [results[i] for i in choices]

        for selected in selected_results:
            print(f"\n→ Selected: {selected['title']}")

            # 3. Add magnet to Real-Debrid and wait for metadata
            print("⏳ Sending to Real-Debrid...")
            try:
                torrent_id = rd.add_magnet(selected["magnet"])
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 451:
                    print("✗ Real-Debrid refused this torrent (451 — unavailable for legal reasons). Try a different release.")
                else:
                    print(f"✗ Real-Debrid error: {e}")
                continue
            info = rd.wait_for_metadata(torrent_id)
            files = info.get("files", [])

            # 4. Pick files (skip if single file)
            if len(files) > 1:
                file_ids = pick_files(files)
                file_count = len(files) if file_ids == "all" else len(file_ids)
            else:
                file_ids = "all"
                file_count = max(len(files), 1)

            # 5. Tell RD which files to prepare
            rd.select_files(torrent_id, file_ids)

            # 6. Add to queue
            queue.append({
                "torrent_id": torrent_id,
                "dest_dir": dest_dir,
                "title": selected["title"],
                "file_count": file_count,
            })

        print_queue(queue)

    if not queue:
        print("Nothing queued.")
        sys.exit(0)

    # --- Resolution phase: wait for all torrents concurrently ---
    print(f"\n⏳ Waiting for {len(queue)} torrent(s) to finish on Real-Debrid...")
    lock = threading.Lock()

    def resolve(item):
        label = item["title"][:30]
        return rd.wait_for_links(item["torrent_id"], label, lock)

    with ThreadPoolExecutor(max_workers=len(queue)) as pool:
        links_per_item = list(pool.map(resolve, queue))

    # --- Unrestrict all links concurrently ---
    print("\n🔗 Unrestricting links...")

    all_link_dest = []
    for item, links in zip(queue, links_per_item):
        for link in links:
            all_link_dest.append((link, item["dest_dir"]))

    def do_unrestrict(link_dest):
        link, dest = link_dest
        result = rd.unrestrict(link)
        return (result["download"], dest, result.get("filename"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        tasks = list(pool.map(do_unrestrict, all_link_dest))

    # --- Download phase ---
    max_dl = cfg.get("max_concurrent_downloads", 3)
    download_concurrent(tasks, max_dl)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nBye.")
        sys.exit(0)
