"""Drive the scanner.

Windows: WIA (Windows Image Acquisition). Installing Epson's driver for the
    Perfection scanner ("Epson Scan" or "Epson Scan 2" from epson.com) also
    installs its WIA driver. Needs `pip install pywin32`.
Linux / macOS: SANE's `scanimage`. The Perfection V850 Pro (04b8:0151) is
    rated "good" by SANE's built-in epson2 backend. Other Epson Perfection models are handled by
    SANE's `epson2`/`epsonds` backends or Epson's "Image Scan! for Linux" /
    "Epson Scan 2" plugins. Whatever shows up in `scanimage -L` works here.

If direct scanning doesn't work on your setup, scan with Epson's own software
and use `photo-pilot watch <its output folder>` instead.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

IS_WINDOWS = sys.platform == "win32"


class ScannerError(RuntimeError):
    pass


def list_devices() -> list[tuple[str, str]]:
    """[(device id to pass as --device, description)]"""
    return _wia_list() if IS_WINDOWS else _sane_list()


def scan(device: str | None = None, dpi: int = 600, extra_args: list[str] | None = None,
         keep_as: Path | None = None) -> np.ndarray:
    """Scan the whole glass in colour and return it as BGR (optionally also saved to `keep_as`)."""
    if IS_WINDOWS:
        from . import config
        tmp = config.app_dir() / "last-scan.bmp"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        _wia_scan(tmp, device, dpi)
        data = np.fromfile(str(tmp), dtype=np.uint8)
        tmp.unlink(missing_ok=True)
    else:
        data = np.frombuffer(_sane_scan(device, dpi, extra_args or []), dtype=np.uint8)
    if keep_as:
        keep_as.parent.mkdir(parents=True, exist_ok=True)
        data.tofile(str(keep_as))
    img = decode_image(data)
    if img is None:
        raise ScannerError("the scanner sent an image that can't be read")
    return img


def raw_suffix() -> str:
    return ".bmp" if IS_WINDOWS else ".tif"


def decode_image(data: np.ndarray) -> np.ndarray | None:
    img = cv2.imdecode(data, cv2.IMREAD_COLOR | cv2.IMREAD_ANYDEPTH)
    if img is not None and img.dtype == np.uint16:
        img = (img / 257).astype(np.uint8)
    return img


def load_image(path: Path) -> np.ndarray | None:
    """Read any image (incl. 16-bit TIFFs from Epson Scan) as 8-bit BGR."""
    # fromfile + imdecode: cv2.imread can't open non-ASCII paths on Windows.
    try:
        return decode_image(np.fromfile(str(path), dtype=np.uint8))
    except OSError:
        return None


# --- SANE (Linux / macOS) -------------------------------------------------

def _scanimage() -> str:
    exe = shutil.which("scanimage")
    if not exe:
        raise ScannerError(
            "`scanimage` not found. Install SANE (Linux: `sudo apt install sane-utils`, "
            "macOS: `brew install sane-backends`), or scan with Epson Scan and use "
            "`photo-pilot watch` instead."
        )
    return exe


def _sane_list() -> list[tuple[str, str]]:
    out = subprocess.run([_scanimage(), "-f", "%d|%v %m%n"], capture_output=True, text=True,
                         timeout=60)
    return [tuple(line.strip().split("|", 1)) for line in out.stdout.splitlines() if "|" in line]


def _sane_scan(device: str | None, dpi: int, extra: list[str]) -> bytes:
    """Scan straight into memory (no temporary file)."""
    cmd = [_scanimage(), "--mode", "Color", "--resolution", str(dpi), "--format", "tiff"]
    if device:
        cmd += ["-d", device]
    cmd += extra
    proc = subprocess.run(cmd, capture_output=True, timeout=900)
    if proc.returncode != 0 or not proc.stdout:
        raise ScannerError(proc.stderr.decode(errors="replace").strip() or "scanimage failed")
    return proc.stdout


# --- WIA (Windows) ---------------------------------------------------------

WIA_SCANNER = 1
WIA_FORMAT_BMP = "{B96B3CAB-0728-11D3-9D7B-0000F81EF32E}"
# WIA property ids
DEV_NAME = "7"
DEV_BED_WIDTH = "3074"  # thousandths of an inch
DEV_BED_HEIGHT = "3075"
DEV_DOC_SELECT = "3088"  # 1 = feeder, 2 = flatbed
ITEM_INTENT = "6146"  # 1 = colour
ITEM_DATATYPE = "4103"  # 3 = colour
ITEM_DEPTH = "4104"
ITEM_XRES = "6147"
ITEM_YRES = "6148"
ITEM_XPOS = "6149"
ITEM_YPOS = "6150"
ITEM_XEXTENT = "6151"
ITEM_YEXTENT = "6152"


def _wia():
    try:
        import win32com.client  # type: ignore
    except ImportError as e:
        raise ScannerError("Windows scanning needs pywin32: run `pip install pywin32`") from e
    try:
        return win32com.client.Dispatch("WIA.DeviceManager")
    except Exception as e:  # pywintypes.com_error
        raise ScannerError(f"Windows Image Acquisition (WIA) is not available: {e}") from e


def _prop(props, pid):
    for p in props:
        if str(p.PropertyID) == pid:
            return p
    return None


def _set(props, pid, value) -> bool:
    p = _prop(props, pid)
    if p is None:
        return False
    try:
        p.Value = value
        return True
    except Exception:
        return False  # driver refused this value; keep its default


def _wia_list() -> list[tuple[str, str]]:
    out = []
    for info in _wia().DeviceInfos:
        if info.Type == WIA_SCANNER:
            name = _prop(info.Properties, DEV_NAME)
            out.append((info.DeviceID, name.Value if name else "scanner"))
    return out


def _wia_scan(path: Path, device: str | None, dpi: int) -> None:
    infos = [i for i in _wia().DeviceInfos if i.Type == WIA_SCANNER]
    if device:
        # Accept the exact WIA device id or part of the name, e.g. "V850".
        infos = [i for i in infos if i.DeviceID == device or device.lower() in
                 str(getattr(_prop(i.Properties, DEV_NAME), "Value", "")).lower()]
    if not infos:
        raise ScannerError("no scanner found" + (f" matching '{device}'" if device else "") +
                           ". Is it switched on, plugged in, and is Epson's driver installed?")
    try:
        dev = infos[0].Connect()
    except Exception as e:
        raise ScannerError(f"cannot connect to the scanner (is Epson Scan still open?): {e}") from e

    _set(dev.Properties, DEV_DOC_SELECT, 2)  # flatbed glass, not the film holder / feeder
    item = dev.Items[1]
    props = item.Properties
    _set(props, ITEM_INTENT, 1)
    _set(props, ITEM_DATATYPE, 3)
    _set(props, ITEM_DEPTH, 24)
    _set(props, ITEM_XRES, dpi)
    _set(props, ITEM_YRES, dpi)
    # Ask for the whole glass; Epson's default area is often just letter/A4 or a preview box.
    bw, bh = _prop(dev.Properties, DEV_BED_WIDTH), _prop(dev.Properties, DEV_BED_HEIGHT)
    _set(props, ITEM_XPOS, 0)
    _set(props, ITEM_YPOS, 0)
    for bed, pid in ((bw, ITEM_XEXTENT), (bh, ITEM_YEXTENT)):
        if bed and not _set(props, pid, int(bed.Value * dpi / 1000)):
            try:
                _set(props, pid, _prop(props, pid).SubTypeMax)
            except Exception:
                pass

    try:
        image = item.Transfer(WIA_FORMAT_BMP)
    except Exception as e:
        raise ScannerError(f"scan failed: {e}") from e
    path.unlink(missing_ok=True)  # WIA refuses to overwrite
    image.SaveFile(str(path))
