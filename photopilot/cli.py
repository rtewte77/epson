"""photo-pilot: scan a glass full of photos, save each photo as its own file.

Commands: scan | import | watch | setup | devices
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from . import config, scanner
from .pipeline import IMAGE_EXTS, Settings, parse_date, process_bed

HELP_PROMPT = """
  [Enter]       scan (swap photos as soon as it says "scanned")
  d 1978-07     date of the next photos (1978 / 1978-07 / 1978-07-14)
  d             back to: date = scan time
  q             quit
"""


def make_settings(args) -> Settings:
    out = args.out or config.load().get("out_dir") or config.default_out_dir()
    s = Settings(
        out_dir=Path(out).expanduser(),
        date=parse_date(args.date) if args.date else None,
        quality=args.quality,
        dpi=getattr(args, "dpi", 600),
        min_area=args.min_area,
        sensitivity=args.sensitivity,
        previews=not args.no_preview,
    )
    print(f"Photos go to: {s.out_dir}")
    return s


def summary(n: int, source: str) -> str:
    if n == 0:
        return f"{source}: no photos found - lid closed? photos on the glass?"
    return f"{source}: {n} photo{'s' if n != 1 else ''} saved"


class Saver:
    """Cuts up and saves scans in the background, so the next scan can start right away."""

    def __init__(self, s: Settings):
        self.s = s
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.lock = threading.Lock()
        self.total = 0
        self.prompt = ""

    def submit(self, img, name: str, label: str) -> None:
        s = Settings(**vars(self.s))  # snapshot: a later 'd' must not change this batch
        self.pool.submit(self._run, img, name, label, s)

    def _run(self, img, name, label, s):
        try:
            n = len(process_bed(img, name, s))
            msg = summary(n, label)
        except Exception as e:  # never lose the loop because of one bad scan
            n, msg = 0, f"{label}: could not save ({e})"
        with self.lock:
            self.total += n
            # Print above the waiting prompt, then show the prompt again.
            line = f"  {msg}".ljust(len(self.prompt))  # fully covers the prompt it overwrites
            print(f"\r{line}\n{self.prompt}", end="", flush=True)

    def finish(self) -> int:
        self.pool.shutdown(wait=True)
        return self.total


def cmd_scan(args) -> None:
    s = make_settings(args)
    extra = args.scanimage_args.split() if args.scanimage_args else []
    saver = Saver(s)
    print("Put photos face down on the glass, about 1 cm apart, and close the lid.")
    print(HELP_PROMPT)
    count = 0
    while True:
        saver.prompt = f"[{f'date {s.date:%Y-%m-%d}' if s.date else 'date = scan time'}] > "
        try:
            line = input(saver.prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        cmd, _, rest = line.partition(" ")
        if cmd in ("q", "quit", "exit"):
            break
        if cmd == "d":
            try:
                s.date = parse_date(rest) if rest.strip() else None
            except ValueError as e:
                print(e)
            continue
        if cmd in ("?", "h", "help"):
            print(HELP_PROMPT)
            continue
        if line:
            print("Unknown command, type ? for help.")
            continue

        count += 1
        name = datetime.now().strftime("%Y%m%d-%H%M%S")
        print(f"Scanning #{count}...", flush=True)
        t0 = time.time()
        keep = (s.out_dir.parent / "photo-pilot raw scans" / f"{name}{scanner.raw_suffix()}"
                if args.keep_raw else None)
        try:
            img = scanner.scan(args.device, s.dpi, extra, keep_as=keep)
        except scanner.ScannerError as e:
            print(f"Scan failed: {e}")
            count -= 1
            continue
        print(f"  scanned in {time.time() - t0:.0f}s - you can put the next photos on now")
        saver.submit(img, name, f"scan #{count}")
    print("Finishing...")
    total = saver.finish()
    print(f"\nDone: {total} photo(s) saved in {s.out_dir}")


def bed_files(paths: list[str]) -> list[Path]:
    files = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.iterdir() if f.suffix.lower() in IMAGE_EXTS)
        elif p.exists():
            files.append(p)
        else:
            print(f"skipping {p}: not found")
    return files


def import_one(path: Path, s: Settings) -> int:
    img = scanner.load_image(path)
    if img is None:
        print(f"{path.name}: cannot read image, skipped")
        return 0
    n = len(process_bed(img, path.stem, s))
    print(summary(n, path.name))
    return n


def cmd_import(args) -> None:
    s = make_settings(args)
    total = sum(import_one(p, s) for p in bed_files(args.paths))
    print(f"Done: {total} photo(s) saved.")


class Watcher:
    """Remembers which scans in a folder were already handled (survives restarts)."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.state = config.app_dir() / "watched.json"
        try:
            self.done = set(json.loads(self.state.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            self.done = set()
        self.sizes: dict[Path, int] = {}

    def poll(self, s: Settings) -> int:
        """Handle every finished new scan once. Returns photos saved."""
        n = 0
        for f in bed_files([str(self.folder)]):
            key = str(f.resolve())
            if key in self.done:
                continue
            size = f.stat().st_size
            # Only once the scanning software has finished writing it.
            if size > 0 and self.sizes.get(f) == size:
                n += import_one(f, s)
                self.done.add(key)
                self.sizes.pop(f, None)
                self.state.parent.mkdir(parents=True, exist_ok=True)
                self.state.write_text(json.dumps(sorted(self.done)), encoding="utf-8")
            else:
                self.sizes[f] = size
        return n


def cmd_watch(args) -> None:
    s = make_settings(args)
    folder = Path(args.folder)
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Watching {folder} for new scans (Ctrl+C to stop)...")
    w = Watcher(folder)
    try:
        while True:
            w.poll(s)
            time.sleep(2)
    except KeyboardInterrupt:
        print()


def cmd_devices(args) -> None:
    try:
        devices = scanner.list_devices()
    except scanner.ScannerError as e:
        sys.exit(str(e))
    if not devices:
        print("No scanners found. Is it switched on and plugged in, and is Epson's driver installed?"
              + ("" if scanner.IS_WINDOWS else " On Linux also try `sudo scanimage -L`."))
    for dev, desc in devices:
        print(f"{desc}\n    --device {dev}")


def cmd_setup(args) -> None:
    """Choose the folder the photos go to (e.g. the folder Immich imports from)."""
    cfg = config.load()
    current = cfg.get("out_dir") or str(config.default_out_dir())
    answer = input(f"Folder for the photos [{current}]: ").strip().strip('"')
    cfg["out_dir"] = answer or current
    config.save(cfg)
    print(f"Photos will go to: {cfg['out_dir']}")


def double_clicked() -> None:
    """Started without options (e.g. double-clicked): go straight to scanning."""
    try:
        try:
            found = scanner.list_devices()
        except scanner.ScannerError:
            found = []
        if found:
            print("Scanner: " + ", ".join(desc for _, desc in found))
            main(["scan"])
            return
        folder = Path.home() / "Pictures" / "Epson Scan"
        print("The scanner can't be used directly (is it on, plugged in, driver installed?).")
        print("Alternative: scan with Epson Scan (whole glass, no auto-crop) and save into")
        print(f"  {folder}")
        answer = input("Watch that folder and cut up each new scan? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes", "j", "ja", "o", "oui"):
            main(["watch", str(folder)])
    except SystemExit as e:
        if e.code not in (None, 0):
            print(e.code)
    except (EOFError, KeyboardInterrupt):
        pass
    finally:
        # Keep the window open so the last messages can be read.
        try:
            input("\nPress Enter to close this window.")
        except (EOFError, KeyboardInterrupt):
            pass


def main(argv: list[str] | None = None) -> None:
    if argv is None and len(sys.argv) == 1:
        return double_clicked()
    p = argparse.ArgumentParser(prog="photo-pilot", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--out", help="folder for the photos (default: chosen in setup, "
                                            "else Pictures/Scanned photos)")
    common.add_argument("--date", help="when the photos were taken: 1985, 1985-07 or 1985-07-14")
    common.add_argument("--quality", type=int, default=95, help="JPEG quality (default 95)")
    common.add_argument("--min-area", type=float, default=0.01,
                        help="ignore blobs smaller than this fraction of the glass (default 0.01)")
    common.add_argument("--sensitivity", type=float, default=1.0,
                        help=">1 finds faint photos better (white borders on white lid), <1 ignores more noise")
    common.add_argument("--no-preview", action="store_true",
                        help="don't keep the small 'what was detected' previews")

    sp = sub.add_parser("scan", parents=[common], help="scan, cut out, save, repeat")
    sp.add_argument("-d", "--device",
                    help="which scanner (see `photo-pilot devices`); on Windows part of the name, e.g. V850")
    sp.add_argument("--dpi", type=int, default=600,
                    help="scan resolution (default 600 = all the detail a print has; 1200 for small prints)")
    sp.add_argument("--keep-raw", action="store_true",
                    help="also keep each full-glass scan (next to the photo folder)")
    sp.add_argument("--scanimage-args", help="Linux/macOS: extra options passed to scanimage, quoted")
    sp.set_defaults(func=cmd_scan)

    ip = sub.add_parser("import", parents=[common], help="cut up existing full-glass scans (files or folders)")
    ip.add_argument("paths", nargs="+")
    ip.set_defaults(func=cmd_import)

    wp = sub.add_parser("watch", parents=[common],
                        help="cut up every new scan that appears in a folder (e.g. Epson Scan's output)")
    wp.add_argument("folder")
    wp.set_defaults(func=cmd_watch)

    sub.add_parser("setup", help="choose the folder the photos go to").set_defaults(func=cmd_setup)
    sub.add_parser("devices", help="list connected scanners").set_defaults(func=cmd_devices)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
