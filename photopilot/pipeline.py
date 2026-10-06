"""Bed scan -> individual JPEGs (with date) -> duplicate check -> Immich."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from . import detect
from .immich import Immich, ImmichError

IMAGE_EXTS = {".tif", ".tiff", ".jpg", ".jpeg", ".png", ".bmp"}
STATE_FILE = ".photo-pilot.json"


def parse_date(text: str) -> datetime:
    """'1985', '1985-07' or '1985-07-14' -> datetime (missing parts = 1, noon)."""
    m = re.fullmatch(r"(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", text.strip())
    if not m:
        raise ValueError(f"date must look like 1985, 1985-07 or 1985-07-14, not {text!r}")
    y, mo, d = (int(g) if g else 1 for g in m.groups())
    return datetime(y, mo, d, 12, 0, 0)


def phash(gray32: np.ndarray) -> int:
    """Perceptual hash of a 32x32 grayscale image: similar pictures give similar bits."""
    low = cv2.dct(gray32.astype(np.float32))[:8, :8].flatten()[1:]  # coarse structure, no DC
    bits = low > np.median(low)
    return int("".join("1" if b else "0" for b in bits), 2)


def rotation_hashes(img: np.ndarray) -> list[int]:
    """Hashes of the photo at 0/90/180/270 deg, so a re-scan in another orientation still matches."""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if img.ndim == 3 else img
    sq = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA)
    return [phash(np.rot90(sq, k).copy()) for k in range(4)]


@dataclass
class Settings:
    out_dir: Path
    album: str | None = None
    date: datetime | None = None
    quality: int = 95
    dpi: int = 600
    min_area: float = 0.01
    sensitivity: float = 1.0
    dedupe_distance: int = 8  # max differing hash bits to call two photos the same
    keep_beds: bool = True
    immich: Immich | None = None


@dataclass
class State:
    """Remembers what was saved / uploaded, so re-runs and retries are safe."""
    path: Path
    hashes: dict[str, int] = field(default_factory=dict)  # photo file -> perceptual hash
    uploaded: dict[str, str] = field(default_factory=dict)  # photo file -> Immich asset id
    taken: dict[str, str] = field(default_factory=dict)  # photo file -> ISO date for Immich
    albums: dict[str, str] = field(default_factory=dict)  # photo file -> album name
    beds: list[str] = field(default_factory=list)  # bed scans already processed

    @classmethod
    def load(cls, out_dir: Path) -> "State":
        path = out_dir / STATE_FILE
        if path.exists():
            raw = json.loads(path.read_text())
            return cls(path=path, **{k: raw.get(k, v) for k, v in
                                     [("hashes", {}), ("uploaded", {}), ("taken", {}),
                                      ("albums", {}), ("beds", [])]})
        return cls(path=path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"hashes": self.hashes, "uploaded": self.uploaded,
                                   "taken": self.taken, "albums": self.albums,
                                   "beds": self.beds}, indent=1))
        tmp.replace(self.path)

    def find_duplicate(self, hashes: list[int], max_dist: int) -> str | None:
        for name, h in self.hashes.items():
            if any(bin(h ^ x).count("1") <= max_dist for x in hashes):
                return name
        return None


def save_jpeg(img: np.ndarray, path: Path, taken: datetime, quality: int, dpi: int) -> None:
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    exif = Image.Exif()
    stamp = taken.strftime("%Y:%m:%d %H:%M:%S")
    exif[0x0131] = "photo-pilot"  # Software
    exif[0x010E] = "Scanned print"  # ImageDescription
    exif[0x0132] = stamp  # DateTime
    sub = exif.get_ifd(0x8769)
    sub[0x9003] = stamp  # DateTimeOriginal -> where Immich puts it on the timeline
    sub[0x9004] = stamp  # DateTimeDigitized
    pil.save(path, "JPEG", quality=quality, dpi=(dpi, dpi), exif=exif.tobytes(), subsampling=0)


@dataclass
class Result:
    saved: list[Path] = field(default_factory=list)
    duplicates: list[tuple[int, str]] = field(default_factory=list)  # (photo no., existing file)
    found: int = 0


def process_bed(scan: np.ndarray, name: str, s: Settings, state: State) -> Result:
    """Cut one bed scan into photos and save the new ones."""
    photos = detect.find_photos(scan, min_area=s.min_area, sensitivity=s.sensitivity)
    res = Result(found=len(photos))
    s.out_dir.mkdir(parents=True, exist_ok=True)
    if s.keep_beds:
        beds = s.out_dir / "beds"
        beds.mkdir(exist_ok=True)
        # imencode + tofile: cv2.imwrite can't handle non-ASCII paths on Windows.
        ok, buf = cv2.imencode(".jpg", detect.debug_overlay(scan, photos),
                               [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            buf.tofile(str(beds / f"{name}-detected.jpg"))

    taken = s.date or datetime.now().replace(microsecond=0)
    for i, photo in enumerate(photos, 1):
        hashes = rotation_hashes(photo.image)
        dup = state.find_duplicate(hashes, s.dedupe_distance)
        if dup:
            res.duplicates.append((i, dup))
            continue
        out = s.out_dir / f"{name}-{i:02d}.jpg"
        save_jpeg(photo.image, out, taken, s.quality, s.dpi)
        state.hashes[out.name] = hashes[0]
        state.taken[out.name] = taken.isoformat()
        if s.album:
            state.albums[out.name] = s.album
        res.saved.append(out)
    state.save()
    return res


def upload_pending(s: Settings, state: State, log=print) -> int:
    """Upload every saved photo that isn't in Immich yet. Returns how many failed."""
    if not s.immich:
        return 0
    pending = sorted(n for n in state.hashes if n not in state.uploaded
                     and (s.out_dir / n).exists())
    failed = 0
    by_album: dict[str, list[str]] = {}
    for name in pending:
        taken = datetime.fromisoformat(state.taken.get(name, datetime.now().isoformat()))
        try:
            asset_id, dup = s.immich.upload(s.out_dir / name, taken)
        except ImmichError as e:
            log(f"  ! upload of {name} failed: {e}")
            failed += 1
            continue
        state.uploaded[name] = asset_id
        state.save()
        if name in state.albums:
            by_album.setdefault(state.albums[name], []).append(asset_id)
        log(f"  -> Immich: {name}" + (" (already there)" if dup else ""))
    for album, ids in by_album.items():
        try:
            s.immich.add_to_album(album, ids)
            log(f"  -> added {len(ids)} to album '{album}'")
        except ImmichError as e:
            log(f"  ! could not add to album '{album}': {e}")
    return failed
