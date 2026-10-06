"""Find the individual photos on a flatbed scan and cut them out.

The scanner lid gives a fairly uniform background (white, or black if you put
a dark sheet behind the photos). Anything that differs from that background,
or that has a strong edge, is treated as "photo". Each connected blob becomes
one picture; its rotated bounding box is used to straighten (deskew) it.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

# Detection runs on a downscaled copy; this is the long side in pixels.
WORK_SIZE = 1200


@dataclass
class Photo:
    image: np.ndarray  # BGR, cropped and straightened, full resolution
    box: np.ndarray  # 4x2 corner points in the original scan
    angle: float  # degrees the photo was tilted on the glass


def _background_color(lab: np.ndarray, strip: int) -> np.ndarray:
    """Median colour of the outer border of the scan = the lid colour."""
    border = np.concatenate([
        lab[:strip].reshape(-1, 3),
        lab[-strip:].reshape(-1, 3),
        lab[:, :strip].reshape(-1, 3),
        lab[:, -strip:].reshape(-1, 3),
    ])
    return np.median(border, axis=0)


def photo_mask(scan: np.ndarray, sensitivity: float = 1.0) -> tuple[np.ndarray, float]:
    """Binary mask (on a downscaled copy) of where photos are.

    Returns the mask and the scale factor from the original to the mask.
    `sensitivity` > 1 picks up fainter photos (e.g. white-bordered prints on a
    white lid) at the cost of more noise.
    """
    h, w = scan.shape[:2]
    scale = min(1.0, WORK_SIZE / max(h, w))
    small = cv2.resize(scan, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (5, 5), 0)

    lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB).astype(np.float32)
    strip = max(3, min(small.shape[:2]) // 100)
    bg = _background_color(lab, strip)

    # 1) colour difference from the lid
    diff = np.linalg.norm(lab - bg, axis=2)
    mask = (diff > 14.0 / sensitivity).astype(np.uint8) * 255

    # 2) edges catch the outline of photos whose border matches the lid colour
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    lo = int(25 / sensitivity)
    edges = cv2.Canny(gray, lo, lo * 3)
    mask |= edges

    # Close gaps and fill each photo into a solid blob.
    k = max(5, min(small.shape[:2]) // 60) | 1
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, k))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    filled = np.zeros_like(mask)
    cv2.drawContours(filled, contours, -1, 255, thickness=cv2.FILLED)
    # Remove dust, scratches and thin lines (e.g. the edge of the glass).
    filled = cv2.morphologyEx(filled, cv2.MORPH_OPEN, kernel)
    return filled, scale


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Order 4 points as top-left, top-right, bottom-right, bottom-left."""
    s = pts.sum(axis=1)
    d = np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]],
                    dtype=np.float32)


def _straighten(scan: np.ndarray, rect, inset: float) -> tuple[np.ndarray, np.ndarray]:
    (cx, cy), (rw, rh), angle = rect
    # Shave a little off each side so no lid colour shows at the edges.
    rw, rh = rw * (1 - 2 * inset), rh * (1 - 2 * inset)
    box = _order_corners(cv2.boxPoints(((cx, cy), (rw, rh), angle)))
    tl, tr, br, bl = box
    out_w = round(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    out_h = round(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.array([[0, 0], [out_w - 1, 0], [out_w - 1, out_h - 1], [0, out_h - 1]], dtype=np.float32)
    m = cv2.getPerspectiveTransform(box, dst)
    warped = cv2.warpPerspective(scan, m, (out_w, out_h), flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
    return warped, box


def find_photos(
    scan: np.ndarray,
    min_area: float = 0.01,
    inset: float = 0.004,
    sensitivity: float = 1.0,
) -> list[Photo]:
    """Return every photo found on the scan, in reading order (top-left first).

    min_area: smallest photo to keep, as a fraction of the whole scan area.
    inset:    fraction trimmed from each side of a photo after straightening.
    """
    mask, scale = photo_mask(scan, sensitivity)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    total = mask.shape[0] * mask.shape[1]

    rects = []
    for c in contours:
        if cv2.contourArea(c) < min_area * total:
            continue
        (cx, cy), (rw, rh), angle = cv2.minAreaRect(c)
        if min(rw, rh) < 10:
            continue
        rects.append(((cx / scale, cy / scale), (rw / scale, rh / scale), angle))

    # Reading order: rows of photos top to bottom, left to right within a row.
    row_h = scan.shape[0] / 8
    rects.sort(key=lambda r: (round(r[0][1] / row_h), r[0][0]))

    photos = []
    for rect in rects:
        img, box = _straighten(scan, rect, inset)
        tilt = (rect[2] + 45) % 90 - 45  # minAreaRect's angle -> tilt in [-45, 45)
        photos.append(Photo(image=img, box=box, angle=float(tilt)))
    return photos


def debug_overlay(scan: np.ndarray, photos: list[Photo]) -> np.ndarray:
    """A copy of the scan with numbered outlines of what was detected."""
    out = scan.copy()
    t = max(2, max(scan.shape[:2]) // 400)
    for i, p in enumerate(photos, 1):
        pts = p.box.astype(np.int32)
        cv2.polylines(out, [pts], True, (0, 0, 255), t)
        x, y = pts.min(axis=0)
        cv2.putText(out, str(i), (int(x) + 6 * t, int(y) + 20 * t), cv2.FONT_HERSHEY_SIMPLEX,
                    t * 0.8, (0, 0, 255), t)
    return out
