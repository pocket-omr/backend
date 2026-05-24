import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from app.core.config import settings

logger = logging.getLogger(__name__)


def send_reset_code_email(to_email: str, code: str) -> None:
    """Send a password reset code via Gmail SMTP."""
    if not settings.smtp_email or not settings.smtp_password:
        logger.warning(
            "SMTP not configured. Reset code for %s: %s", to_email, code
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Pocket OMR - Password Reset Code: {code}"
    msg["From"] = settings.smtp_email
    msg["To"] = to_email

    html = (
        '<div style="font-family: Segoe UI, Arial, sans-serif;'
        ' max-width: 480px; margin: 0 auto; padding: 32px;">'
        '<div style="text-align: center; margin-bottom: 24px;">'
        '<h2 style="color: #053B76; margin: 0;">'
        'Pocket <span style="color: #0B96D9;">OMR</span></h2></div>'
        '<div style="background: #f4faff; border: 2px solid #ceedf8;'
        ' border-radius: 16px; padding: 32px; text-align: center;">'
        '<p style="color: #053B76; font-size: 1rem; margin: 0 0 8px;">'
        "Your password reset code is:</p>"
        '<div style="font-size: 2.5rem; font-weight: 700;'
        f' color: #0B96D9; letter-spacing: 8px; margin: 16px 0;">'
        f"{code}</div>"
        '<p style="color: #6B8DB2; font-size: 0.85rem; margin: 16px 0 0;">'
        "This code expires in 15 minutes."
        " If you didn't request this, ignore this email.</p>"
        "</div></div>"
    )

    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(settings.smtp_email, settings.smtp_password)
            server.sendmail(settings.smtp_email, to_email, msg.as_string())
        logger.info("Reset code email sent to %s", to_email)
    except Exception:
        logger.exception("Failed to send reset code email to %s", to_email)
        raise
