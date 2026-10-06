"""Minimal Immich API client: upload assets and put them in an album.

Create an API key in Immich under Account Settings -> API Keys. It needs at
least the asset.upload, album.read, album.create and albumAsset.create
permissions (or just "all").
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
        self._req("GET", "/server/ping")
        self._req("GET", "/users/me")

    def upload(self, path: Path, taken: datetime) -> tuple[str, bool]:
        """Upload one file. Returns (asset id, was_duplicate)."""
        data = path.read_bytes()
        stamp = taken.isoformat()
        fields = {
            "deviceAssetId": f"{path.name}-{len(data)}",
            "deviceId": DEVICE_ID,
            "fileCreatedAt": stamp,
            "fileModifiedAt": stamp,
        }
        # Lets Immich spot a file it already has without receiving it again.
        headers = {"x-immich-checksum": hashlib.sha1(data).hexdigest()}
        r = self._req("POST", "/assets", data=fields, headers=headers,
                      files={"assetData": (path.name, data, "image/jpeg")})
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
