"""Clara / harici geri çağrı — isteğe bağlı HTTP webhook."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx


class ClaraClient:
    """Backend sonuçlarını isteğe bağlı olarak harici URL'ye iletir."""

    def __init__(self) -> None:
        self.callback_url = os.environ.get("CLARA_CALLBACK_URL", "").strip().rstrip("/")
        self.callback_key = os.environ.get("CLARA_CALLBACK_KEY", "").strip()

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.callback_key:
            h["X-API-Key"] = self.callback_key
        return h

    def send_result(self, task_id: str | None, result: dict[str, Any]) -> bool:
        if not self.callback_url:
            return False
        payload = {"task_id": task_id, "result": result}
        try:
            with httpx.Client(timeout=30.0) as client:
                r = client.post(
                    f"{self.callback_url}/result",
                    headers=self._headers(),
                    content=json.dumps(payload),
                )
            return r.is_success
        except Exception:
            return False

    def send_screenshot(self, image_path: str) -> bool:
        if not self.callback_url:
            return False
        payload = {"path": image_path}
        try:
            with httpx.Client(timeout=60.0) as client:
                r = client.post(
                    f"{self.callback_url}/screenshot",
                    headers=self._headers(),
                    content=json.dumps(payload),
                )
            return r.is_success
        except Exception:
            return False

    def send_notification(self, message: str) -> bool:
        if not self.callback_url:
            return False
        payload = {"message": message}
        try:
            with httpx.Client(timeout=15.0) as client:
                r = client.post(
                    f"{self.callback_url}/notify",
                    headers=self._headers(),
                    content=json.dumps(payload),
                )
            return r.is_success
        except Exception:
            return False

    def request_command(self) -> dict[str, Any] | None:
        """Uzaktan bekleyen komut yoksa None (polling — isteğe bağlı)."""
        if not self.callback_url:
            return None
        try:
            with httpx.Client(timeout=10.0) as client:
                r = client.get(
                    f"{self.callback_url}/next",
                    headers=self._headers(),
                )
            if r.is_success and r.content:
                return r.json()
        except Exception:
            pass
        return None
