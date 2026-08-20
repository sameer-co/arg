"""Sends messages to a Telegram chat via the Bot API."""
import logging

import requests

import config

log = logging.getLogger("notifier")


class TelegramNotifier:
    def __init__(self, test_mode: bool = False):
        self.test_mode = test_mode
        if not test_mode and (not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID):
            log.warning(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — reports will be "
                "logged locally instead of sent to Telegram."
            )

    def send(self, text: str):
        if self.test_mode or not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
            log.info("[telegram-disabled] %s", text.replace("\n", " | "))
            return

        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            resp = requests.post(url, json=payload, timeout=config.HTTP_TIMEOUT_SEC)
            if resp.status_code != 200:
                log.error("Telegram send failed (%s): %s", resp.status_code, resp.text[:300])
        except requests.RequestException as exc:
            log.error("Telegram send error: %s", exc)
