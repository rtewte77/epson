"""Command line: photo-pilot scan | import | watch | upload | devices"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import scanner
from .immich import Immich, ImmichError
from .pipeline import IMAGE_EXTS, Settings, State, parse_date, process_bed, upload_pending

HELP_PROMPT = """
  [Enter]        scan the photos on the glass
  d 1978-07      set the date for the next photos (1978, 1978-07 or 1978-07-14; 'd' alone clears)
  a Holiday 78   put the next photos in this Immich album ('a' alone clears)
  u              retry uploads that failed
  q              quit
"""


def report(res, source: str) -> None:
    print(f"{source}: found {res.found} photo(s), saved {len(res.saved)}"
          + (f", skipped {len(res.duplicates)} already scanned" if res.duplicates else ""))
    for n, existing in res.duplicates:
        print(f"  = photo {n} looks like {existing}")
    if res.found == 0:
        print("  (nothing detected - is the lid closed and are the photos on the glass?)")


def make_settings(args) -> Settings:
    s = Settings(
        out_dir=Path(args.out),
        album=args.album,
        date=parse_date(args.date) if args.date else None,
        quality=args.quality,
        dpi=getattr(args, "dpi", 600),
        min_area=args.min_area,
        sensitivity=args.sensitivity,
        keep_beds=not args.no_debug,
    )
    if not args.no_upload:
        url = args.immich_url or os.environ.get("IMMICH_URL")
        key = args.api_key or os.environ.get("IMMICH_API_KEY")
        if not url or not key:
            sys.exit("Immich not configured: pass --immich-url and --api-key "
                     "(or set IMMICH_URL / IMMICH_API_KEY), or use --no-upload.")
        s.immich = Immich(url, key)
        try:
            s.immich.check()
        except ImmichError as e:
            sys.exit(f"Immich check failed: {e}")
        print(f"Connected to Immich at {url}")
    return s


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


def cmd_scan(args) -> None:
    s = make_settings(args)
    state = State.load(s.out_dir)
    extra = args.scanimage_args.split() if args.scanimage_args else []
    print("Put photos face down on the glass with ~1 cm between them, then close the lid.")
    print(HELP_PROMPT)
    while True:
        tag = (f"date={s.date:%Y-%m-%d}" if s.date else "date=scan time") + \
              (f", album={s.album}" if s.album else "")
        try:
            line = input(f"[{tag}] > ").strip()
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
        if cmd == "a":
            s.album = rest.strip() or None
            continue
        if cmd == "u":
            upload_pending(s, state)
            continue
        if cmd in ("?", "h", "help"):
            print(HELP_PROMPT)
            continue
        if line:
            print("Unknown command, type ? for help.")
            continue

        name = datetime.now().strftime("%Y%m%d-%H%M%S")
        print("Scanning...")
        t0 = time.time()
        raw = s.out_dir / "beds" / f"{name}{scanner.scan_suffix()}"
        try:
            img = scanner.scan(raw, args.device, s.dpi, extra)
        except scanner.ScannerError as e:
            print(f"Scan failed: {e}")
            continue
        res = process_bed(img, name, s, state)
        if not args.keep_raw:
            raw.unlink(missing_ok=True)  # full-glass scans are ~100 MB each
        report(res, f"Scan done in {time.time() - t0:.0f}s")
        upload_pending(s, state)
    failed = upload_pending(s, state)
    print(f"Bye. {len(state.uploaded)} photo(s) in Immich so far"
          + (f", {failed} still waiting to upload (run `photo-pilot upload`)." if failed else "."))


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


def import_one(path: Path, s: Settings, state: State) -> None:
    img = scanner.load_image(path)
    if img is None:
        print(f"{path.name}: cannot read image, skipped")
        return
    res = process_bed(img, path.stem, s, state)
    state.beds.append(str(path.resolve()))
    state.save()
    report(res, path.name)


def cmd_import(args) -> None:
    s = make_settings(args)
    state = State.load(s.out_dir)
    for path in bed_files(args.paths):
        if str(path.resolve()) in state.beds and not args.again:
            print(f"{path.name}: already imported (use --again to redo)")
            continue
        import_one(path, s, state)
        upload_pending(s, state)


def cmd_watch(args) -> None:
    s = make_settings(args)
    state = State.load(s.out_dir)
    folder = Path(args.folder)
    print(f"Watching {folder} for new scans (Ctrl+C to stop)...")
    sizes: dict[Path, int] = {}
    try:
        while True:
            for f in bed_files([str(folder)]):
                if str(f.resolve()) in state.beds:
                    continue
                size = f.stat().st_size
                # Only pick it up once the scanner software has finished writing it.
                if size > 0 and sizes.get(f) == size:
                    import_one(f, s, state)
                    upload_pending(s, state)
                    sizes.pop(f, None)
                else:
                    sizes[f] = size
            time.sleep(2)
    except KeyboardInterrupt:
        print()


def cmd_upload(args) -> None:
    s = make_settings(args)
    state = State.load(s.out_dir)
    failed = upload_pending(s, state)
    print("All uploaded." if not failed else f"{failed} upload(s) failed.")


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="photo-pilot", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--out", default="photos", help="where cut-out photos are saved (default: ./photos)")
    common.add_argument("--immich-url", help="e.g. http://192.168.1.10:2283 (or env IMMICH_URL)")
    common.add_argument("--api-key", help="Immich API key (or env IMMICH_API_KEY)")
    common.add_argument("--no-upload", action="store_true", help="only save files locally")
    common.add_argument("--album", help="Immich album to put the photos in")
    common.add_argument("--date", help="when the photos were taken: 1985, 1985-07 or 1985-07-14")
    common.add_argument("--quality", type=int, default=95, help="JPEG quality (default 95)")
    common.add_argument("--min-area", type=float, default=0.01,
                        help="ignore blobs smaller than this fraction of the glass (default 0.01)")
    common.add_argument("--sensitivity", type=float, default=1.0,
                        help=">1 finds faint photos better (white borders on white lid), <1 ignores more noise")
    common.add_argument("--no-debug", action="store_true",
                        help="don't keep the detection previews in <out>/beds")

    sub.add_parser("devices", help="list connected scanners").set_defaults(func=cmd_devices)

    sp = sub.add_parser("scan", parents=[common], help="interactive: scan, split, upload, repeat")
    sp.add_argument("-d", "--device",
                    help="which scanner (see `photo-pilot devices`); on Windows part of the name, e.g. V850")
    sp.add_argument("--keep-raw", action="store_true", help="keep the full-glass scans in <out>/beds")
    sp.add_argument("--dpi", type=int, default=600, help="scan resolution (default 600; 1200 for small prints)")
    sp.add_argument("--scanimage-args", help="Linux/macOS: extra options passed to scanimage, quoted")
    sp.set_defaults(func=cmd_scan)

    ip = sub.add_parser("import", parents=[common], help="split existing bed scans (files or folders)")
    ip.add_argument("paths", nargs="+")
    ip.add_argument("--again", action="store_true", help="re-process scans imported before")
    ip.set_defaults(func=cmd_import)

    wp = sub.add_parser("watch", parents=[common],
                        help="process every new scan that appears in a folder (e.g. Epson Scan's output folder)")
    wp.add_argument("folder")
    wp.set_defaults(func=cmd_watch)

    sub.add_parser("upload", parents=[common], help="retry uploads that failed").set_defaults(func=cmd_upload)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
