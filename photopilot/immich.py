"""Minimal Immich API client: upload assets and put them in an album.

Create an API key in Immich under Account Settings -> API Keys. It needs at
least the asset.upload, album.read, album.create and albumAsset.create
permissions (or just "all").

Uploads use the Immich v3 form fields; older servers that still require
deviceAssetId/deviceId are detected on the first upload and handled.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

import requests

DEVICE_ID = "photo-pilot"


class ImmichError(RuntimeError):
    pass


class Immich:
    def __init__(self, url: str, api_key: str, timeout: float = 120):
        self.base = url.rstrip("/")
        if not self.base.endswith("/api"):
            self.base += "/api"
        self.session = requests.Session()
        self.session.headers.update({"x-api-key": api_key, "Accept": "application/json"})
        self.timeout = timeout
        self._album_ids: dict[str, str] = {}
        self._legacy_upload = False  # Immich before v3 wants deviceAssetId/deviceId

    def _req(self, method: str, path: str, **kw) -> requests.Response:
        try:
            r = self.session.request(method, self.base + path, timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise ImmichError(f"cannot reach Immich at {self.base}: {e}") from e
        if r.status_code >= 400:
            raise ImmichError(f"{method} {path} -> HTTP {r.status_code}: {r.text[:300]}")
        return r

    def check(self) -> None:
        """Fail early with a clear message if the URL or API key is wrong."""
        self._req("GET", "/server/ping")  # is this Immich at all?
        self._req("GET", "/albums")  # is the key valid? (uses a permission we need anyway)

    def upload(self, path: Path, taken: datetime) -> tuple[str, bool]:
        """Upload one file. Returns (asset id, was_duplicate)."""
        data = path.read_bytes()
        stamp = taken.isoformat()
        fields = {"fileCreatedAt": stamp, "fileModifiedAt": stamp, "filename": path.name}
        # Lets Immich spot a file it already has without receiving it again.
        headers = {"x-immich-checksum": hashlib.sha1(data).hexdigest()}

        def send():
            extra = ({"deviceAssetId": f"{path.name}-{len(data)}", "deviceId": DEVICE_ID}
                     if self._legacy_upload else {})
            return self._req("POST", "/assets", data={**fields, **extra}, headers=headers,
                             files={"assetData": (path.name, data, "image/jpeg")})

        try:
            r = send()
        except ImmichError as e:
            if self._legacy_upload or "HTTP 400" not in str(e) or "device" not in str(e):
                raise
            self._legacy_upload = True  # older server: retry the way it expects
            r = send()
        body = r.json()
        return body["id"], body.get("status") == "duplicate"

    def album_id(self, name: str) -> str:
        if name not in self._album_ids:
            for album in self._req("GET", "/albums").json():
                if album.get("albumName") == name:
                    self._album_ids[name] = album["id"]
                    break
            else:
                created = self._req("POST", "/albums", json={"albumName": name}).json()
                self._album_ids[name] = created["id"]
        return self._album_ids[name]

    def add_to_album(self, name: str, asset_ids: list[str]) -> None:
        if asset_ids:
            self._req("PUT", f"/albums/{self.album_id(name)}/assets", json={"ids": asset_ids})
