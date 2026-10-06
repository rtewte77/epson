import cv2
import numpy as np
import pytest

from photopilot.detect import find_photos

RNG = np.random.default_rng(0)


def fake_photo(w, h, white_border=False):
    """A noisy 'picture' so it has texture like a real print."""
    # Large shapes (like people / horizon) over fine grain, as on a real print.
    img = np.zeros((h, w, 3), np.uint8)
    img[:] = RNG.integers(60, 200, 3)
    for _ in range(6):
        c = tuple(int(v) for v in RNG.integers(30, 230, 3))
        x, y = int(RNG.integers(0, w)), int(RNG.integers(0, h))
        cv2.circle(img, (x, y), int(RNG.integers(min(w, h) // 8, min(w, h) // 3)), c, -1)
    grain = RNG.integers(-20, 21, size=img.shape)
    img = cv2.GaussianBlur(np.clip(img + grain, 0, 255).astype(np.uint8), (5, 5), 0)
    if white_border:
        b = max(8, w // 20)
        img[:b] = img[-b:] = 248
        img[:, :b] = img[:, -b:] = 248
    return img


def place(bed, photo, center, angle):
    """Paste `photo` rotated by `angle` degrees at `center` on `bed`, with a faint shadow edge."""
    h, w = photo.shape[:2]
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    m[0, 2] += center[0] - w / 2
    m[1, 2] += center[1] - h / 2
    size = (bed.shape[1], bed.shape[0])
    warped = cv2.warpAffine(photo, m, size, flags=cv2.INTER_LINEAR)
    mask = cv2.warpAffine(np.full((h, w), 255, np.uint8), m, size)
    edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    bed[mask > 0] = warped[mask > 0]
    bed[edge > 0] = (bed[edge > 0] * 0.8).astype(np.uint8)


def make_bed(bg, white_border=False):
    # Roughly an A4 bed at 150 dpi.
    bed = np.full((1750, 1275, 3), bg, np.uint8)
    specs = [((350, 400), (500, 350), 3), ((950, 420), (400, 550), -7),
             ((330, 1200), (450, 600), 0), ((930, 1250), (520, 380), 12)]
    for center, (w, h), angle in specs:
        place(bed, fake_photo(w, h, white_border), center, angle)
    noise = RNG.normal(0, 2, bed.shape)
    return np.clip(bed + noise, 0, 255).astype(np.uint8), specs


@pytest.mark.parametrize("bg,white_border", [(245, False), (245, True), (15, False), (15, True)])
def test_finds_each_photo(bg, white_border):
    bed, specs = make_bed(bg, white_border)
    photos = find_photos(bed)
    assert len(photos) == len(specs)
    # Reading order: top row left-to-right, then bottom row.
    for photo, (_, (w, h), angle) in zip(photos, specs):
        ph, pw = photo.image.shape[:2]
        assert sorted((pw, ph)) == pytest.approx(sorted((w, h)), rel=0.05)
        assert abs(abs(photo.angle) - abs(angle)) < 2


def test_empty_bed_finds_nothing():
    bed = np.clip(np.full((1750, 1275, 3), 245.0) + RNG.normal(0, 2, (1750, 1275, 3)), 0, 255)
    assert find_photos(bed.astype(np.uint8)) == []


def test_ignores_dust():
    bed, specs = make_bed(245)
    for _ in range(40):
        x, y = RNG.integers(0, 1275), RNG.integers(0, 1750)
        cv2.circle(bed, (int(x), int(y)), 3, (60, 60, 60), -1)
    assert len(find_photos(bed)) == len(specs)
