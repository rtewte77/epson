import builtins
import time

import cv2
import numpy as np
import pytest
from PIL import Image

from photopilot import config, scanner
from photopilot.cli import Watcher, main
from photopilot.pipeline import Settings, parse_date

from .test_detect import make_bed


def write_png(path, img):
    ok, buf = cv2.imencode(".png", img)
    buf.tofile(str(path))


def test_parse_date():
    assert parse_date("1985").isoformat() == "1985-01-01T12:00:00"
    assert parse_date("1985-07").month == 7
    assert parse_date("1985-07-14").day == 14
    with pytest.raises(ValueError):
        parse_date("July 85")


def test_import_saves_full_resolution_photos(tmp_path):
    bed, specs = make_bed(245)
    write_png(tmp_path / "bed1.png", bed)
    out = tmp_path / "photos"
    main(["import", str(tmp_path / "bed1.png"), "--out", str(out), "--date", "1978-07"])

    photos = sorted(out.iterdir())
    # Only the photos land in the folder: no previews, temp files or bookkeeping.
    assert [p.name for p in photos] == [f"bed1-{i:02d}.jpg" for i in range(1, len(specs) + 1)]
    for p, (_, (w, h), _) in zip(photos, specs):
        im = Image.open(p)
        assert sorted(im.size) == pytest.approx(sorted((w, h)), rel=0.05)  # not downscaled
        assert im.getexif().get_ifd(0x8769)[0x9003] == "1978:07:01 12:00:00"
    assert (config.preview_dir() / "bed1.jpg").exists()


def test_batch_rerun_only_does_new_scans(tmp_path):
    bed, specs = make_bed(15)
    scans = tmp_path / "scans"
    (scans / "box 2").mkdir(parents=True)
    write_png(scans / "a.png", bed)
    write_png(scans / "box 2" / "b.png", bed)  # subfolders are included
    out = tmp_path / "photos"
    main(["import", str(scans), "--out", str(out)])
    assert len(list(out.glob("*.jpg"))) == 2 * len(specs)

    main(["import", str(scans), "--out", str(out)])  # nothing new: nothing done
    assert len(list(out.glob("*.jpg"))) == 2 * len(specs)

    write_png(scans / "c.png", bed)  # a new scan: only that one
    main(["import", str(scans), "--out", str(out)])
    assert len(list(out.glob("*.jpg"))) == 3 * len(specs)

    main(["import", str(scans), "--out", str(out), "--again"])  # redo all, never overwrite
    assert len(list(out.glob("*.jpg"))) == 6 * len(specs)


def test_batch_skips_its_own_output_and_survives_bad_files(tmp_path, capsys):
    bed, specs = make_bed(245)
    scans = tmp_path / "Pictures"
    scans.mkdir()
    write_png(scans / "glass.png", bed)
    (scans / "broken.jpg").write_bytes(b"not an image")
    out = scans / "Scanned photos"  # output folder inside the scan folder
    main(["import", str(scans), "--out", str(out)])
    main(["import", str(scans), "--out", str(out)])
    assert len(list(out.glob("*.jpg"))) == len(specs)  # cut-outs were not cut up again
    assert "broken.jpg: FAILED" in capsys.readouterr().out


def test_drag_and_drop_folder(tmp_path, monkeypatch):
    bed, specs = make_bed(245)
    write_png(tmp_path / "scan.png", bed)
    out = tmp_path / "photos"
    config.save({"out_dir": str(out)})
    monkeypatch.setattr("sys.argv", ["photo-pilot-windows.exe", str(tmp_path)])
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    main()
    assert len(list(out.glob("*.jpg"))) == len(specs)


def test_folder_names_with_accents(tmp_path):
    """e.g. C:\\Users\\Jürgen\\Pictures\\Fotos Ölberg - must work on Windows too."""
    bed, specs = make_bed(245)
    src = tmp_path / "Fotos Ölberg"
    src.mkdir()
    write_png(src / "Scan été.png", bed)
    out = tmp_path / "Jürgen" / "Bilder"
    main(["import", str(src), "--out", str(out)])
    assert len(list(out.glob("*.jpg"))) == len(specs)


def test_scan_loop_saves_in_background(tmp_path, monkeypatch):
    bed, specs = make_bed(245)
    calls = []

    def fake_scan(device, dpi, extra, keep_as=None):
        calls.append(dpi)
        time.sleep(0.05)
        return bed.copy()

    monkeypatch.setattr(scanner, "scan", fake_scan)
    answers = iter(["", "d 1979-08", "", "q"])
    monkeypatch.setattr(builtins, "input", lambda prompt="": next(answers))
    out = tmp_path / "photos"
    main(["scan", "--out", str(out)])

    photos = sorted(out.glob("*.jpg"))
    assert len(photos) == 2 * len(specs) and calls == [600, 600]
    dates = {Image.open(p).getexif().get_ifd(0x8769)[0x9003] for p in photos}
    assert "1979:08:01 12:00:00" in dates and len(dates) == 2  # 1st scan: scan time, 2nd: 1979


def test_watch_handles_each_scan_once(tmp_path):
    bed, specs = make_bed(245)
    inbox = tmp_path / "Epson Scan"
    inbox.mkdir()
    write_png(inbox / "scan1.png", bed)
    s = Settings(out_dir=tmp_path / "photos")
    w = Watcher(inbox)
    assert w.poll(s) == 0  # first sighting: wait until the file stops growing
    assert w.poll(s) == len(specs)
    assert w.poll(s) == 0
    assert Watcher(inbox).poll(s) == 0  # remembered after a restart


def test_setup_remembers_folder(tmp_path, monkeypatch):
    target = tmp_path / "Immich import"
    monkeypatch.setattr(builtins, "input", lambda prompt="": f'"{target}"')
    main(["setup"])
    bed, specs = make_bed(245)
    write_png(tmp_path / "b.png", bed)
    main(["import", str(tmp_path / "b.png")])  # no --out: uses the saved folder
    assert len(list(target.glob("*.jpg"))) == len(specs)
