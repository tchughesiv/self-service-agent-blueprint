"""Slack integration handler."""

import os
from typing import Any, Dict

from shared_db.models import DeliveryStatus, UserIntegrationConfig
from slack_sdk.errors import SlackApiError
from slack_sdk.web.async_client import AsyncWebClient

from ..schemas import DeliveryRequest
from .base import BaseIntegrationHandler, IntegrationResult


class SlackIntegrationHandler(BaseIntegrationHandler):
    """Handler for Slack message delivery."""

    def __init__(self):
        super().__init__()
        self.bot_token = os.getenv("SLACK_BOT_TOKEN")
        self.client = AsyncWebClient(token=self.bot_token) if self.bot_token else None

    async def deliver(
        self,
        request: DeliveryRequest,
        config: UserIntegrationConfig,
        template_content: Dict[str, str],
    ) -> IntegrationResult:
        """Deliver message to Slack."""
        if not self.client:
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message="Slack bot token not configured",
            )

        try:
            slack_config = config.config

            # Determine target channel
            channel = slack_config.get("channel_id")
            if not channel:
                # Try to find user's DM channel
                user_email = slack_config.get("user_email")
                if user_email:
                    channel = await self._get_user_dm_channel(user_email)
                else:
                    return IntegrationResult(
                        success=False,
                        status=DeliveryStatus.FAILED,
                        message="No channel_id or user_email configured",
                    )

            # Build message blocks
            blocks = self._build_message_blocks(
                template_content.get("body", ""),
                request,
                slack_config,
            )

            # Send message
            response = await self.client.chat_postMessage(
                channel=channel,
                text=template_content.get("subject", "Agent Response"),
                blocks=blocks,
                thread_ts=(
                    slack_config.get("thread_ts")
                    if slack_config.get("thread_replies")
                    else None
                ),
            )

            if response["ok"]:
                return IntegrationResult(
                    success=True,
                    status=DeliveryStatus.DELIVERED,
                    message="Message delivered to Slack",
                    metadata={
                        "channel": response["channel"],
                        "ts": response["ts"],
                        "message_id": response.get("message", {}).get("ts"),
                    },
                )
            else:
                return IntegrationResult(
                    success=False,
                    status=DeliveryStatus.FAILED,
                    message=f"Slack API error: {response.get('error', 'Unknown error')}",
                )

        except SlackApiError as e:
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message=f"Slack API error: {e.response['error']}",
                retry_after=60 if e.response.get("error") == "rate_limited" else None,
            )
        except Exception as e:
            return IntegrationResult(
                success=False,
                status=DeliveryStatus.FAILED,
                message=f"Unexpected error: {str(e)}",
            )

    async def validate_config(self, config: Dict[str, Any]) -> bool:
        """Validate Slack configuration."""
        # Must have either channel_id or user_email
        has_channel = bool(config.get("channel_id"))
        has_user_email = bool(config.get("user_email"))

        if not (has_channel or has_user_email):
            return False

        # Validate channel_id format if provided
        if has_channel:
            channel_id = config["channel_id"]
            if not (
                channel_id.startswith("C")
                or channel_id.startswith("D")
                or channel_id.startswith("G")
            ):
                return False

        return True

    async def health_check(self) -> bool:
        """Check Slack API connectivity."""
        if not self.client:
            return False

        try:
            response = await self.client.auth_test()
            return response["ok"]
        except Exception:
            return False

    def get_required_config_fields(self) -> list[str]:
        """Required Slack configuration fields."""
        return []  # Either channel_id or user_email is required, but not both

    def get_optional_config_fields(self) -> list[str]:
        """Optional Slack configuration fields."""
        return [
            "channel_id",
            "user_email",
            "workspace_id",
            "thread_replies",
            "mention_user",
            "include_agent_info",
            "thread_ts",
        ]

    async def _get_user_dm_channel(self, user_email: str) -> str:
        """Get or create DM channel with user."""
        try:
            # Find user by email
            users_response = await self.client.users_lookupByEmail(email=user_email)
            if not users_response["ok"]:
                raise Exception(f"User not found: {user_email}")

            user_id = users_response["user"]["id"]

            # Open DM channel
            dm_response = await self.client.conversations_open(users=[user_id])
            if not dm_response["ok"]:
                raise Exception("Failed to open DM channel")

            return dm_response["channel"]["id"]

        except Exception as e:
            raise Exception(f"Failed to get DM channel: {str(e)}")

    def _build_message_blocks(
        self,
        content: str,
        request: DeliveryRequest,
        config: Dict[str, Any],
    ) -> list[Dict[str, Any]]:
        """Build Slack message blocks."""
        blocks = []

        # Main content block
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": content,
                },
            }
        )

        # Agent info if enabled
        if config.get("include_agent_info") and request.agent_id:
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"_Response from agent: {request.agent_id}_",
                        }
                    ],
                }
            )

        # Add divider
        blocks.append({"type": "divider"})

        # Action buttons
        buttons = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "📋 View Session"},
                "value": request.session_id,
                "action_id": "view_session",
                "style": "primary",
            }
        ]

        # Add follow-up button if this looks like it might need one
        content_lower = content.lower()
        if any(
            word in content_lower
            for word in ["help", "assist", "question", "need", "issue"]
        ):
            buttons.append(
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "💬 Ask Follow-up"},
                    "value": f"followup:{request.session_id}",
                    "action_id": "ask_followup",
                }
            )

        blocks.append({"type": "actions", "elements": buttons})

        return blocks
