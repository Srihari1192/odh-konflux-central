"""KubeArchive REST client for archived Tekton resources."""

from __future__ import annotations

import json
import sys
from typing import Any

from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


class KubeArchiveClient:
    def __init__(self, host: str, token: str) -> None:
        self.host = host.rstrip("/")
        parsed = urlparse(self.host)
        if parsed.scheme != "https":
            raise ValueError(f"Unsupported KubeArchive host scheme: {parsed.scheme!r}")
        if not parsed.netloc:
            raise ValueError("KubeArchive host must include a valid hostname")
        self.host = f"https://{parsed.netloc}{parsed.path}".rstrip("/")
        self.token = token
        self.available: bool | None = None

    def _request(self, path: str) -> str:
        req = Request(
            f"{self.host}{path}",
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        try:
            with urlopen(req, timeout=20) as resp:
                return resp.read().decode("utf-8")
        except HTTPError as exc:
            print(f"WARN KubeArchive HTTP {exc.code} for GET {path}", file=sys.stderr)
            return ""
        except URLError as exc:
            reason = getattr(exc, "reason", exc)
            print(f"WARN KubeArchive request failed for GET {path}: {reason}", file=sys.stderr)
            return ""

    def check(self) -> bool:
        if self.available is None:
            raw = self._request("/livez")
            try:
                self.available = bool(raw and json.loads(raw).get("code") == 200)
            except json.JSONDecodeError:
                self.available = False
        return bool(self.available)

    def get_json(self, path: str) -> dict[str, Any]:
        raw = self._request(path)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def get_text(self, path: str) -> str:
        return self._request(path)
