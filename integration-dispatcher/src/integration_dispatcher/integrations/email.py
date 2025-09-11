"""Email integration handler."""

import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict

import aiosmtplib
from shared_db.models import DeliveryStatus, UserIntegrationConfig

from ..schemas import DeliveryRequest
from .base import BaseIntegrationHandler, IntegrationResult


class EmailIntegrationHandler(BaseIntegrationHandler):
    """Handler for email delivery."""

    def __init__(self):
        super().__init__()
        self.smtp_host = os.getenv("SMTP_HOST", "localhost")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username = os.getenv("SMTP_USERNAME")
        self.smtp_password = os.getenv("SMTP_PASSWORD")
        self.smtp_use_tls = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
        self.from_email = os.getenv("FROM_EMAIL", "noreply@selfservice.local")
        self.from_name = os.getenv("FROM_NAME", "Self-Service Agent")

    async def deliver(
        self,
        request: DeliveryRequest,
        config: UserIntegrationConfig,
        template_content: Dict[str, str],
    ) -> IntegrationResult:
        """Deliver message via email."""
        try:
            email_config = config.config
            recipient_email = email_config.get("email_address")

            if not recipient_email:
                return IntegrationResult(
                    success=False,
                    status=DeliveryStatus.FAILED,
                    message="No email address configured",
                )

            # Create message
            msg = MIMEMultipart("alternative")
            msg["Subject"] = template_content.get("subject", "Agent Response")
            msg["From"] = f"{self.from_name} <{self.from_email}>"
            msg["To"] = recipient_email

            # Add reply-to if configured
            reply_to = email_config.get("reply_to")
            if reply_to:
                msg["Reply-To"] = reply_to

            # Add custom headers
            msg["X-Request-ID"] = request.request_id
            msg["X-Session-ID"] = request.session_id
            msg["X-Agent-ID"] = request.agent_id or "unknown"

            # Create email content
            email_format = email_config.get("format", "html")
            if email_format == "html":
                html_content = self._create_html_content(
                    template_content.get("body", ""),
                    request,
                    email_config,
                )
                msg.attach(MIMEText(html_content, "html"))
            else:
                text_content = self._create_text_content(
                    template_content.get("body", ""),
                    request,
                    email_config,
                )
                msg.attach(MIMEText(text_content, "plain"))

            # Send email
            await aiosmtplib.send(
                msg,
                hostname=self.smtp_host,
                port=self.smtp_port,
                username=self.smtp_username,
                password=self.smtp_password,
                use_tls=self.smtp_use_tls,
            )

            return IntegrationResult(
                success=True,
                status=DeliveryStatus.DELIVERED,
                message="Email sent successfully",
                metadata={
                    "recipient": recipient_email,
                    "subject": msg["Subject"],
                },
            )

        except aiosmtplib.SMTPException as e:
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message=f"SMTP error: {str(e)}",
                retry_after=300,  # Retry after 5 minutes
            )
        except Exception as e:
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message=f"Unexpected error: {str(e)}",
            )

    async def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate email configuration."""
        email_address = config.get("email_address")
        if not email_address or "@" not in email_address:
            return False

        # Validate format if specified
        email_format = config.get("format", "html")
        if email_format not in ["html", "text"]:
            return False

        return True

    async def health_check(self) -> bool:
        """Check SMTP connectivity."""
        try:
            async with aiosmtplib.SMTP(
                hostname=self.smtp_host,
                port=self.smtp_port,
                use_tls=self.smtp_use_tls,
            ) as smtp:
                if self.smtp_username and self.smtp_password:
                    await smtp.login(self.smtp_username, self.smtp_password)
                return True
        except Exception:
            return False

    def get_required_config_fields(self) -> list[str]:
        """Required email configuration fields."""
        return ["email_address"]

    def get_optional_config_fields(self) -> list[str]:
        """Optional email configuration fields."""
        return [
            "display_name",
            "format",
            "include_signature",
            "reply_to",
        ]

    def _create_html_content(
        self,
        content: str,
        request: DeliveryRequest,
        config: Dict[str, Any],
    ) -> str:
        """Create HTML email content."""
        # Convert markdown-like content to HTML
        html_content = content.replace("\n", "<br>")

        # Basic HTML template
        html = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                .header {{ background-color: #f8f9fa; padding: 20px; border-radius: 5px; margin-bottom: 20px; }}
                .content {{ padding: 20px; }}
                .footer {{ margin-top: 30px; padding: 15px; background-color: #f8f9fa; font-size: 0.9em; color: #666; }}
                .agent-info {{ font-style: italic; color: #666; margin-top: 15px; }}
            </style>
        </head>
        <body>
            <div class="header">
                <h2>Self-Service Agent Response</h2>
            </div>

            <div class="content">
                {html_content}
            </div>

            {self._get_agent_info_html(request) if config.get("include_agent_info", True) else ""}

            <div class="footer">
                <p>Request ID: {request.request_id}</p>
                <p>Session ID: {request.session_id}</p>
                {self._get_signature_html() if config.get("include_signature", True) else ""}
            </div>
        </body>
        </html>
        """

        return html

    def _create_text_content(
        self,
        content: str,
        request: DeliveryRequest,
        config: Dict[str, Any],
    ) -> str:
        """Create plain text email content."""
        text_parts = [
            "Self-Service Agent Response",
            "=" * 30,
            "",
            content,
            "",
        ]

        if config.get("include_agent_info", True) and request.agent_id:
            text_parts.extend(
                [
                    f"Response from agent: {request.agent_id}",
                    "",
                ]
            )

        text_parts.extend(
            [
                "-" * 30,
                f"Request ID: {request.request_id}",
                f"Session ID: {request.session_id}",
            ]
        )

        if config.get("include_signature", True):
            text_parts.extend(
                [
                    "",
                    "This is an automated message from the Self-Service Agent system.",
                ]
            )

        return "\n".join(text_parts)

    def _get_agent_info_html(self, request: DeliveryRequest) -> str:
        """Get HTML for agent information."""
        if not request.agent_id:
            return ""

        return f'<div class="agent-info">Response from agent: {request.agent_id}</div>'

    def _get_signature_html(self) -> str:
        """Get HTML signature."""
        return "<p><em>This is an automated message from the Self-Service Agent system.</em></p>"
