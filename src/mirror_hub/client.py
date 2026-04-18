"""HubClient — HTTP 客户端，调远程 Mirror Hub 的 API 管理 Chrome profiles。

Endpoints used (see hub.py):
  POST /<profile_id>/launch   → start Chrome, returns {ok, port, already_running?}
  GET  /<profile_id>/cdp      → {cdp_url, port, alive, platform, fingerprint_index}
  POST /<profile_id>/stop     → kill Chrome
  POST /<profile_id>/restart  → restart
  GET  /<profile_id>/status   → profile status
  GET  /api/batch-status      → status of all profiles

Auth: Bearer token via `Authorization` header.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any


class HubClient:
    """Remote Mirror Hub HTTP client."""

    def __init__(self, base_url: str, token: str, timeout: float = 10.0):
        """
        Args:
            base_url: Hub root, e.g. "http://h.tommlly.cc:8329" (WITHOUT trailing /mirror)
            token: Bearer token (the sha256-of-password used by hub.py, 32 hex chars)
            timeout: default HTTP timeout seconds
        """
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.timeout = timeout

    # ---------- low-level ----------

    def _request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
        url = f"{self.base_url}/{path.lstrip('/')}"
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header('Authorization', f'Bearer {self.token}')
        if data is not None:
            req.add_header('Content-Type', 'application/json')
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode() or '{}')
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read().decode() or '{}')
            except Exception:
                err = {'error': str(e)}
            err['_status'] = e.code
            return err

    # ---------- public API ----------

    def launch(self, profile_id: str, wait_ready: bool = True, wait_timeout: float = 30.0) -> dict:
        """Ask hub to start Chrome for the profile. Returns info dict with cdp_url.

        Args:
            wait_ready: if True, poll until CDP alive (recommended for most use cases)
            wait_timeout: max seconds to wait for CDP alive
        """
        resp = self._request('POST', f'/{profile_id}/launch')
        if resp.get('_status', 200) >= 400:
            raise RuntimeError(f"launch {profile_id} failed: {resp}")

        if wait_ready and not resp.get('already_running'):
            self._wait_cdp_alive(profile_id, wait_timeout)

        return self.cdp(profile_id)

    def cdp(self, profile_id: str) -> dict:
        """Get CDP info for a profile: {cdp_url, port, alive, platform, fingerprint_index}"""
        resp = self._request('GET', f'/{profile_id}/cdp')
        if resp.get('_status', 200) >= 400:
            raise RuntimeError(f"cdp {profile_id} failed: {resp}")
        return resp

    def stop(self, profile_id: str) -> dict:
        """Kill Chrome for the profile."""
        return self._request('POST', f'/{profile_id}/stop')

    def restart(self, profile_id: str) -> dict:
        """Kill + relaunch."""
        return self._request('POST', f'/{profile_id}/restart')

    def status(self, profile_id: str) -> dict:
        return self._request('GET', f'/{profile_id}/status')

    def batch_status(self) -> dict:
        """Status of all profiles."""
        return self._request('GET', '/api/batch-status')

    # ---------- helpers ----------

    def _wait_cdp_alive(self, profile_id: str, timeout: float) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = self.cdp(profile_id)
            if info.get('alive'):
                return
            time.sleep(1)
        raise TimeoutError(f"CDP never became alive for {profile_id} within {timeout}s")
