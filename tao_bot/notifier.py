"""Notifications Telegram (optionnelles)."""
from __future__ import annotations

import logging
import os

import requests

log = logging.getLogger(__name__)


class Notifier:
    """Envoie un message Telegram si TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID sont définis."""

    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str) -> None:
        log.info(text)
        if not self.enabled:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            ).raise_for_status()
        except requests.RequestException as exc:
            log.warning("Échec de l'envoi Telegram : %s", exc)
