"""텔레그램 sendMessage 전용 (getUpdates 미사용, 재시도 없음 — STD-PUB-03)."""

from __future__ import annotations

from typing import Any

import requests

TIMEOUT_SEC = 15


class TelegramError(RuntimeError):
    """발송 실패(메시지에 토큰 미포함, STD-SEC-02)."""


class TelegramClient:
    def __init__(self, token: str, chat_id: str, session: Any | None = None):
        self._token = token
        self._chat_id = chat_id
        self._session = session or requests.Session()
        self.sent: list[str] = []

    def send(self, text: str) -> None:
        url = f"https://api.telegram.org/bot{self._token}/sendMessage"
        try:
            resp = self._session.post(
                url,
                data={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": "true"},
                timeout=TIMEOUT_SEC,
            )
        except requests.RequestException as exc:
            raise TelegramError(f"sendMessage 연결 실패: {type(exc).__name__}") from None
        ok = False
        try:
            ok = resp.status_code == 200 and bool(resp.json().get("ok"))
        except ValueError:
            ok = False
        if not ok:
            raise TelegramError(f"sendMessage HTTP {resp.status_code}")
        self.sent.append(text)
