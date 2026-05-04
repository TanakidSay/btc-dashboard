from __future__ import annotations

import logging

import requests

from .config import Settings

logger = logging.getLogger(__name__)


def send_notification(alert: dict[str, str], settings: Settings) -> bool:
    if not settings.notification_webhook_url:
        logger.info("Notification skipped because NOTIFICATION_WEBHOOK_URL is not configured")
        return False

    payload = {
        "title": "Bitcoin Dashboard Alert",
        "type": alert["type"],
        "severity": alert["severity"],
        "message": alert["message"],
        "alert": alert,
    }

    try:
        response = requests.post(
            settings.notification_webhook_url,
            json=payload,
            timeout=settings.request_timeout,
        )
        response.raise_for_status()
    except requests.RequestException:
        logger.exception("Failed to send alert notification")
        return False

    return True
