"""Notification channel abstraction.

Supports three transport kinds, picked via the NOTIFIER_KIND env var:
- "bark"        — iOS push via the Bark app (https://bark.day.app)
- "serverchan"  — WeChat push via Server 酱 (https://sct.ftqq.com)
- "smtp"        — generic email via SMTP+STARTTLS
- "none"        — disabled (build_notifier returns a no-op)

Sends are best-effort: failures are logged and swallowed so a flaky transport
never blocks the rest of the system (e.g. recap generation, scheduler ticks).
"""

import smtplib
from email.message import EmailMessage
from typing import Protocol

import httpx

from marketpulse.config import Settings
from marketpulse.logging import get_logger

log = get_logger(__name__)


class Notifier(Protocol):
    def send(self, title: str, body: str, url: str | None = None) -> bool: ...


class NoopNotifier:
    def send(self, title: str, body: str, url: str | None = None) -> bool:
        log.info("notifier_noop", title=title)
        return False


class BarkNotifier:
    """Pushes to the Bark iOS app.

    Bark URLs look like https://api.day.app/<deviceKey>/. We POST to
    <base>/push with JSON body for arbitrary text + URL.
    """

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def send(self, title: str, body: str, url: str | None = None) -> bool:
        try:
            resp = httpx.post(
                f"{self.base_url}/push",
                json={
                    "title": title,
                    "body": body,
                    "url": url,
                    "group": "MarketPulse",
                },
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.warning("notifier_bark_failed", error=str(exc))
            return False


class ServerChanNotifier:
    """Pushes to WeChat via Server 酱 (方糖).

    Endpoint: https://sctapi.ftqq.com/<SendKey>.send with form fields
    `title` and `desp` (markdown body).
    """

    def __init__(self, send_key: str) -> None:
        self.send_key = send_key

    def send(self, title: str, body: str, url: str | None = None) -> bool:
        try:
            desp = body
            if url:
                desp = f"{body}\n\n[查看详情]({url})"
            resp = httpx.post(
                f"https://sctapi.ftqq.com/{self.send_key}.send",
                data={"title": title, "desp": desp},
                timeout=10,
            )
            resp.raise_for_status()
            return True
        except Exception as exc:
            log.warning("notifier_serverchan_failed", error=str(exc))
            return False


class SmtpNotifier:
    """Plain SMTP+STARTTLS email."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        user: str,
        password: str,
        from_addr: str,
        to_addr: str,
    ) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.from_addr = from_addr
        self.to_addr = to_addr

    def send(self, title: str, body: str, url: str | None = None) -> bool:
        try:
            msg = EmailMessage()
            msg["Subject"] = f"[MarketPulse] {title}"
            msg["From"] = self.from_addr
            msg["To"] = self.to_addr
            content = body
            if url:
                content = f"{body}\n\n{url}"
            msg.set_content(content)
            with smtplib.SMTP(self.host, self.port, timeout=15) as s:
                s.starttls()
                s.login(self.user, self.password)
                s.send_message(msg)
            return True
        except Exception as exc:
            log.warning("notifier_smtp_failed", error=str(exc))
            return False


def build_notifier(settings: Settings) -> Notifier:
    kind = (settings.notifier_kind or "none").lower().strip()
    if kind == "bark" and settings.notifier_bark_url:
        return BarkNotifier(settings.notifier_bark_url)
    if kind == "serverchan" and settings.notifier_serverchan_key:
        return ServerChanNotifier(settings.notifier_serverchan_key)
    if kind == "smtp" and settings.notifier_smtp_host and settings.notifier_email_to:
        return SmtpNotifier(
            host=settings.notifier_smtp_host,
            port=settings.notifier_smtp_port,
            user=settings.notifier_smtp_user,
            password=settings.notifier_smtp_password,
            from_addr=settings.notifier_email_from or settings.notifier_smtp_user,
            to_addr=settings.notifier_email_to,
        )
    return NoopNotifier()
