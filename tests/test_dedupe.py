"""Duplicate check: re-scans must match, different-but-similar snapshots must not."""
import cv2
import numpy as np

from photopilot.pipeline import Settings, State, process_bed

from .test_detect import place


def snapshot(rng, w, h, sky, ground, border):
    """A faded holiday snapshot: sky, horizon, sun, a few people."""
    img = np.zeros((h, w, 3), np.uint8)
    hz = int(h * rng.uniform(0.45, 0.6))
    for y in range(h):
        t = y / h
        img[y] = np.array(sky) * (1 - 0.3 * t) if y < hz else np.array(ground) * (0.8 + 0.3 * (y - hz) / h)
    cv2.circle(img, (int(w * .75), int(h * .2)), h // 10, (120, 220, 250), -1)
    for i in range(int(rng.integers(2, 4))):
        x, s = int(w * (0.25 + 0.2 * i)), int(h * rng.uniform(.18, .26))
        col = tuple(int(v) for v in rng.integers(40, 200, 3))
        cv2.ellipse(img, (x, hz + s // 2), (s // 4, s // 2), 0, 0, 360, col, -1)
        cv2.circle(img, (x, hz - s // 6), s // 7, (150, 180, 220), -1)
    img = np.clip(img + rng.integers(-12, 13, img.shape), 0, 255).astype(np.uint8)
    img = (cv2.GaussianBlur(img, (5, 5), 0) * 0.85 + np.array([20, 35, 50])).clip(0, 255)
    img = img.astype(np.uint8)
    if border:
        b = w // 18
        img[:b] = img[-b:] = 238
        img[:, :b] = img[:, -b:] = 238
    return img


def beach_bed():
    """Five similar-looking beach photos on the glass (the case that once fooled a 63-bit hash)."""
    rng = np.random.default_rng(7)
    bed = np.full((1754, 1240, 3), 246, np.uint8)
    for c, (w, h), a, sky, ground, border in [
            ((330, 330), (480, 340), 4, (230, 190, 140), (70, 140, 90), True),
            ((920, 360), (400, 520), -6, (220, 200, 170), (60, 110, 160), False),
            ((310, 900), (460, 330), -2, (210, 170, 120), (40, 90, 140), False),
            ((900, 1000), (440, 600), 9, (200, 180, 150), (90, 130, 60), True),
            ((420, 1430), (520, 360), 2, (235, 205, 160), (80, 160, 200), False)]:
        place(bed, snapshot(rng, w, h, sky, ground, border), c, a)
    return np.clip(bed + rng.normal(0, 2, bed.shape), 0, 255).astype(np.uint8)


def test_lookalike_snapshots_are_not_duplicates(tmp_path):
    res = process_bed(beach_bed(), "bed", Settings(out_dir=tmp_path), State.load(tmp_path))
    assert res.found == 5
    assert len(res.saved) == 5, res.duplicates


def test_rescan_is_duplicate_but_kept(tmp_path):
    bed = beach_bed()
    s, state = Settings(out_dir=tmp_path), State.load(tmp_path)
    process_bed(bed, "first", s, state)
    # Same prints again: turned around, a bit brighter, different noise.
    rng = np.random.default_rng(3)
    again = np.rot90(np.clip(bed * 1.03 + rng.normal(0, 4, bed.shape), 0, 255).astype(np.uint8), 2)
    res = process_bed(again.copy(), "second", s, state)
    assert res.found == 5 and not res.saved and len(res.duplicates) == 5
    assert len(list((tmp_path / "duplicates").glob("second-*.jpg"))) == 5
