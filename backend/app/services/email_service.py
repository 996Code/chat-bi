import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

FRONTEND_URL = "http://localhost:5173"


def _smtp_configured() -> bool:
    return bool(settings.smtp_host)


async def send_email(to: str, subject: str, html_body: str) -> bool:
    if not _smtp_configured():
        logger.info(f"SMTP not configured — would send to {to}: {subject}")
        return False
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        async with aiosmtplib.SMTP(hostname=settings.smtp_host, port=settings.smtp_port) as smtp:
            if settings.smtp_user:
                await smtp.login(settings.smtp_user, settings.smtp_password)
            await smtp.send_message(msg)
        return True
    except Exception:
        logger.exception(f"Failed to send email to {to}")
        return False


async def send_verification_email(to: str, token: str) -> bool:
    link = f"{FRONTEND_URL}/verify-email?token={token}"
    html = f"""
    <h2>验证您的 ChatBI 邮箱</h2>
    <p>请点击以下链接验证您的邮箱地址：</p>
    <p><a href="{link}">验证邮箱</a></p>
    <p>链接有效期 24 小时。</p>
    """
    return await send_email(to, "ChatBI - 验证您的邮箱", html)


async def send_password_reset_email(to: str, token: str) -> bool:
    link = f"{FRONTEND_URL}/reset-password?token={token}"
    html = f"""
    <h2>重置您的 ChatBI 密码</h2>
    <p>请点击以下链接重置密码：</p>
    <p><a href="{link}">重置密码</a></p>
    <p>链接有效期 30 分钟，过期请重新申请。</p>
    """
    return await send_email(to, "ChatBI - 重置密码", html)
