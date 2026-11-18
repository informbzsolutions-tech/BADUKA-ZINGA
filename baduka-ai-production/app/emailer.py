import logging
import smtplib
from email.message import EmailMessage

from app.config import settings

logger = logging.getLogger(__name__)


def send_action_link(recipient: str, subject: str, link: str) -> None:
    if not settings.smtp_host:
        if settings.app_env == "development":
            logger.warning("Development-only account link for %s: %s", recipient, link)
            return
        raise RuntimeError("SMTP is not configured")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.smtp_from
    message["To"] = recipient
    message.set_content(f"Abra esta ligação para continuar: {link}\n\nSe não pediu esta ação, ignore esta mensagem.")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
        if settings.smtp_use_tls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password or "")
        smtp.send_message(message)
