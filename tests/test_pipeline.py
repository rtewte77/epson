import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2
import numpy as np
import pytest
from PIL import Image

from photopilot.cli import main
from photopilot.pipeline import State, parse_date

from .test_detect import make_bed


class FakeImmich(BaseHTTPRequestHandler):
    assets: list = []
    albums: dict = {}

    def log_message(self, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        return self.rfile.read(int(self.headers.get("Content-Length", 0)))

    def do_GET(self):
        if self.headers.get("x-api-key") != "secret":
            return self._send(401, {"message": "bad key"})
        if self.path == "/api/albums":
            return self._send(200, [{"id": k, "albumName": v["name"]} for k, v in self.albums.items()])
        self._send(200, {"res": "pong"})

    def do_POST(self):
        body = self._body()
        if self.path == "/api/assets":
            assert b'name="deviceAssetId"' in body and b'name="fileCreatedAt"' in body
            checksum = self.headers["x-immich-checksum"]
            for a in self.assets:
                if a == checksum:
                    return self._send(200, {"id": f"a{self.assets.index(a)}", "status": "duplicate"})
            self.assets.append(checksum)
            return self._send(201, {"id": f"a{len(self.assets) - 1}", "status": "created"})
        if self.path == "/api/albums":
            aid = f"album{len(self.albums)}"
            self.albums[aid] = {"name": json.loads(body)["albumName"], "assets": []}
            return self._send(201, {"id": aid})
        self._send(404, {})

    def do_PUT(self):
        aid = self.path.split("/")[3]
        self.albums[aid]["assets"] += json.loads(self._body())["ids"]
        self._send(200, [])


@pytest.fixture
def immich():
    FakeImmich.assets, FakeImmich.albums = [], {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeImmich)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_parse_date():
    assert parse_date("1985").isoformat() == "1985-01-01T12:00:00"
    assert parse_date("1985-07").month == 7
    assert parse_date("1985-07-14").day == 14
    with pytest.raises(ValueError):
        parse_date("July 85")


def test_import_upload_and_dedupe(tmp_path, immich):
    bed, specs = make_bed(245)
    scans = tmp_path / "scans"
    scans.mkdir()
    cv2.imwrite(str(scans / "bed1.tif"), bed)
    out = tmp_path / "photos"
    base = ["--out", str(out), "--immich-url", immich, "--api-key", "secret"]

    main(["import", str(scans), *base, "--album", "Holiday 1978", "--date", "1978-07"])

    photos = sorted(out.glob("bed1-*.jpg"))
    assert len(photos) == len(specs)
    exif = Image.open(photos[0]).getexif()
    assert exif.get_ifd(0x8769)[0x9003] == "1978:07:01 12:00:00"
    assert len(FakeImmich.assets) == len(specs)
    (album,) = FakeImmich.albums.values()
    assert album["name"] == "Holiday 1978" and len(album["assets"]) == len(specs)
    assert (out / "beds" / "bed1-detected.jpg").exists()

    # Same folder again: skipped as already imported.
    main(["import", str(scans), *base])
    assert len(FakeImmich.assets) == len(specs)

    # Same photos re-scanned (slightly different noise, one turned upside down): recognised as duplicates.
    rescan = np.clip(bed.astype(int) + np.random.default_rng(1).integers(-3, 4, bed.shape), 0, 255)
    rescan = np.rot90(rescan.astype(np.uint8), 2).copy()
    cv2.imwrite(str(scans / "bed2.tif"), rescan)
    main(["import", str(scans / "bed2.tif"), *base])
    assert not list(out.glob("bed2-*.jpg"))
    assert len(FakeImmich.assets) == len(specs)


def test_failed_upload_is_retried(tmp_path, immich, monkeypatch):
    bed, specs = make_bed(15)
    f = tmp_path / "bed.png"
    cv2.imwrite(str(f), bed)
    out = tmp_path / "photos"
    main(["import", str(f), "--out", str(out), "--no-upload"])
    state = State.load(out)
    assert len(state.hashes) == len(specs) and not state.uploaded

    main(["upload", "--out", str(out), "--immich-url", immich, "--api-key", "secret"])
    assert len(State.load(out).uploaded) == len(specs)


def test_bad_api_key_stops_early(tmp_path, immich):
    with pytest.raises(SystemExit, match="Immich check failed"):
        main(["upload", "--out", str(tmp_path), "--immich-url", immich, "--api-key", "nope"])


def test_setup_then_commands_use_saved_settings(tmp_path, immich, monkeypatch):
    from photopilot import config, scanner
    monkeypatch.setattr(scanner, "list_devices", lambda: [("x", "EPSON Perfection V850")])
    answers = iter([immich, "wrong-key",  # first try fails the check
                    "", "secret",          # Enter keeps the URL, then the right key
                    str(tmp_path / "keep")])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    main(["setup"])
    cfg = config.load()
    assert cfg == {"immich_url": immich, "api_key": "secret", "out_dir": str(tmp_path / "keep")}

    bed, specs = make_bed(245)
    cv2.imwrite(str(tmp_path / "bed.png"), bed)
    main(["import", str(tmp_path / "bed.png")])  # no options: everything from the saved settings
    assert len(list((tmp_path / "keep").glob("bed-*.jpg"))) == len(specs)
    assert len(FakeImmich.assets) == len(specs)
