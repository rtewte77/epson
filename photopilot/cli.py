"""Command line: photo-pilot scan | import | watch | upload | devices"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config, scanner
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
    cfg = config.load()
    s = Settings(
        out_dir=Path(args.out or cfg.get("out_dir") or config.default_out_dir()).expanduser(),
        album=args.album,
        date=parse_date(args.date) if args.date else None,
        quality=args.quality,
        dpi=getattr(args, "dpi", 600),
        min_area=args.min_area,
        sensitivity=args.sensitivity,
        keep_beds=not args.no_debug,
    )
    if not args.no_upload:
        url = args.immich_url or os.environ.get("IMMICH_URL") or cfg.get("immich_url")
        key = args.api_key or os.environ.get("IMMICH_API_KEY") or cfg.get("api_key")
        if not url or not key:
            sys.exit("Immich not configured: run `photo-pilot setup`, pass --immich-url and "
                     "--api-key (or set IMMICH_URL / IMMICH_API_KEY), or use --no-upload.")
        s.immich = Immich(url, key)
        try:
            s.immich.check()
        except ImmichError as e:
            sys.exit(f"Immich check failed: {e}")
        print(f"Connected to Immich at {url}")
    print(f"Photos are saved in: {s.out_dir}")
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


def cmd_setup(args) -> None:
    """Ask for the Immich details once and remember them."""
    cfg = config.load()
    print("photo-pilot setup (press Enter to keep the value in [brackets])\n")

    def ask(question, current, secret=False):
        shown = ("..." + current[-4:]) if secret and current else (current or "")
        answer = input(f"{question} [{shown}]: ").strip()
        return answer or current

    url, key = cfg.get("immich_url"), cfg.get("api_key")
    while True:
        url = ask("Immich address, e.g. http://192.168.1.10:2283", url)
        key = ask("Immich API key (Account Settings > API Keys)", key, secret=True)
        if not url or not key:
            print("Both are needed.\n")
            continue
        try:
            Immich(url, key).check()
        except ImmichError as e:
            print(f"That didn't work: {e}\n")
            continue
        break
    print("Connected to Immich.")
    out = ask("Folder to also keep the photos in", cfg.get("out_dir") or str(config.default_out_dir()))
    cfg.update(immich_url=url, api_key=key, out_dir=out)
    print(f"Saved to {config.save(cfg)}\n")

    try:
        devices = scanner.list_devices()
    except scanner.ScannerError as e:
        devices = []
        print(e)
    if devices:
        print("Scanner found: " + ", ".join(desc for _, desc in devices))
    else:
        print("No scanner found yet. Switch it on, plug it in and install Epson's driver for it.")


def double_clicked() -> None:
    """Started without options (e.g. double-clicked): set up once, then scan."""
    try:
        if not config.load().get("api_key"):
            cmd_setup(None)
        try:
            found = scanner.list_devices()
        except scanner.ScannerError:
            found = []
        if found:
            main(["scan"])
            return
        folder = Path.home() / "Pictures" / "Epson Scan"
        print("\nThe scanner can't be used directly (is it on, plugged in, driver installed?).")
        print("Alternative: scan with Epson Scan (whole glass, no auto-crop, 600 dpi) and save into")
        print(f"  {folder}")
        answer = input("Watch that folder and handle each new scan? [Y/n] ").strip().lower()
        if answer in ("", "y", "yes", "j", "ja", "o", "oui"):
            folder.mkdir(parents=True, exist_ok=True)
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
    p = argparse.ArgumentParser(prog="photo-pilot", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-o", "--out",
                        help="where cut-out photos are saved (default: Pictures/Scanned photos)")
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
    sub.add_parser("setup", help="enter the Immich address and API key once").set_defaults(func=cmd_setup)

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
