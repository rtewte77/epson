"""Bed scan -> one full-resolution JPEG per photo, dropped into the output folder."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import config, detect

IMAGE_EXTS = {".tif", ".tiff", ".jpg", ".jpeg", ".png", ".bmp"}
PREVIEWS_KEPT = 30


def parse_date(text: str) -> datetime:
    """'1985', '1985-07' or '1985-07-14' -> datetime (missing parts = 1, noon)."""
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text.strip())
    if not m:
        raise ValueError(f"date must look like 1985, 1985-07 or 1985-07-14, not {text!r}")
    y, mo, d = (int(g) if g else 1 for g in m.groups())
    return datetime(y, mo, d, 12, 0, 0)


@dataclass
class Settings:
    out_dir: Path
    date: datetime | None = None  # when the photos were taken; None = scan time
    quality: int = 95
    dpi: int = 600
    min_area: float = 0.01
    sensitivity: float = 1.0
    previews: bool = True


def save_jpeg(img: np.ndarray, path: Path, taken: datetime, quality: int, dpi: int) -> None:
    """Write the photo at full resolution, atomically.

    The file appears under its final name only once complete, so a folder watcher
    (Immich external library, a sync tool) never picks up a half-written photo.
    """
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    exif = Image.Exif()
    stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
    exif[0x0131] = "photo-pilot"  # Software
    exif[0x010E] = "Scanned print"  # ImageDescription
    exif[0x0132] = stamp  # DateTime
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = stamp  # DateTimeOriginal: where Immich puts it on the timeline
    sub[0x9004] = stamp  # DateTimeDigitized
    tmp = path.with_name(f".{path.stem}.part")
    # 4:4:4 (no colour subsampling) keeps fine colour detail of the print.
    pil.save(tmp, "JPEG", quality=quality, dpi=(dpi, dpi), exif=exif.tobytes(), subsampling=0)
    os.replace(tmp, path)


def unique_path(folder: Path, stem: str) -> Path:
    path = folder / f"{stem}.jpg"
    n = 2
    while path.exists():
        path = folder / f"{stem}_{n}.jpg"
        n += 1
    return path


def save_preview(scan: np.ndarray, photos: list[detect.Photo], name: str) -> Path | None:
    """Small picture of the glass with the detected photos outlined (kept outside the
    photo folder, so it never ends up in Immich). Only the latest few are kept."""
    folder = config.preview_dir()
    folder.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", detect.debug_overlay(scan, photos), [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        return None
    path = folder / f"{name}.jpg"
    buf.tofile(str(path))  # tofile: cv2.imwrite can't handle non-ASCII paths on Windows
    for old in sorted(folder.glob("*.jpg"), key=lambda p: p.stat().st_mtime)[:-PREVIEWS_KEPT]:
        old.unlink(missing_ok=True)
    return path


def process_bed(scan: np.ndarray, name: str, s: Settings) -> list[Path]:
    """Cut one bed scan into photos and save each one. Returns the saved files."""
    photos = detect.find_photos(scan, min_area=s.min_area, sensitivity=s.sensitivity)
    if s.previews:
        save_preview(scan, photos, name)
    s.out_dir.mkdir(parents=True, exist_ok=True)
    taken = s.date or datetime.now().replace(microsecond=0)
    saved = []
    for i, photo in enumerate(photos, 1):
        path = unique_path(s.out_dir, f"{name}-{i:02d}")
        save_jpeg(photo.image, path, taken, s.quality, s.dpi)
        saved.append(path)
    return saved
