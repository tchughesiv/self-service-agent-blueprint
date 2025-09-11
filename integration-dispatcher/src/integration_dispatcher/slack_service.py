"""Slack service for handling events and interactions."""

import hashlib
import hmac
import os
import time
from typing import Dict, Optional

import httpx
import structlog

from .slack_schemas import SlackInteractionPayload, SlackSlashCommand

logger = structlog.get_logger()


class SlackService:
    """Service for handling Slack events and interactions."""

    def __init__(self):
        self.signing_secret = os.getenv("SLACK_SIGNING_SECRET")
        self.request_manager_url = os.getenv(
            "REQUEST_MANAGER_URL", "http://self-service-agent-request-manager"
        )

    def verify_slack_signature(
        self, body: bytes, timestamp: str, signature: str
    ) -> bool:
        """Verify Slack request signature."""
        if not self.signing_secret:
            logger.warning("Slack signing secret not configured, skipping verification")
            return True

        # Check timestamp to prevent replay attacks
        current_time = int(time.time())
        if abs(current_time - int(timestamp)) > 300:  # 5 minutes
            logger.warning("Slack request timestamp too old", timestamp=timestamp)
            return False

        # Create signature
        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8')}"
        expected_signature = (
            "v0="
            + hmac.new(
                self.signing_secret.encode(), sig_basestring.encode(), hashlib.sha256
            ).hexdigest()
        )

        return hmac.compare_digest(expected_signature, signature)

    async def handle_message_event(self, event: Dict) -> None:
        """Handle incoming Slack message."""
        try:
            # Skip bot messages to avoid loops
            if event.get("bot_id") or event.get("subtype") == "bot_message":
                return

            user_id = event.get("user")
            text = event.get("text", "").strip()
            channel = event.get("channel")
            thread_ts = event.get("thread_ts") or event.get("ts")

            if not text or not user_id:
                return

            # Remove bot mentions from text
            text = self._clean_message_text(text)

            logger.info(
                "Processing Slack message",
                user_id=user_id,
                channel=channel,
                text=text[:100],  # Log first 100 chars
            )

            # Forward to Request Manager
            await self._forward_to_request_manager(
                user_id=user_id,
                content=text,
                integration_type="slack",
                metadata={
                    "slack_channel": channel,
                    "slack_thread_ts": thread_ts,
                    "source": "slack_message",
                },
            )

        except Exception as e:
            logger.error("Error handling Slack message", error=str(e), event=event)

    async def handle_slash_command(self, command: SlackSlashCommand) -> Dict:
        """Handle Slack slash command."""
        try:
            if not command.text.strip():
                return {
                    "response_type": "ephemeral",
                    "text": "👋 Hi! Please include your request after the command.\n"
                    "Example: `/agent I need help with my laptop refresh`",
                }

            logger.info(
                "Processing Slack slash command",
                user_id=command.user_id,
                command=command.command,
                text=command.text[:100],
            )

            # Forward to Request Manager
            await self._forward_to_request_manager(
                user_id=command.user_id,
                content=command.text,
                integration_type="slack",
                metadata={
                    "slack_channel": command.channel_id,
                    "slack_response_url": command.response_url,
                    "source": "slash_command",
                },
            )

            return {
                "response_type": "ephemeral",
                "text": "🚀 Your request has been submitted! I'll send you a response shortly.",
            }

        except Exception as e:
            logger.error(
                "Error handling slash command", error=str(e), command=command.dict()
            )
            return {
                "response_type": "ephemeral",
                "text": "❌ Sorry, there was an error processing your request. Please try again.",
            }

    async def handle_button_interaction(self, payload: SlackInteractionPayload) -> Dict:
        """Handle button interactions."""
        try:
            logger.info(
                "Button interaction received",
                user_id=payload.user.id,
                actions=payload.actions,
                payload_type=payload.type,
            )
            
            if not payload.actions:
                logger.warning("No actions in button interaction")
                return {"text": "No action specified"}

            action = payload.actions[0]
            action_id = action.get("action_id")
            action_value = action.get("value", "")
            
            logger.info(
                "Processing button action",
                action_id=action_id,
                action_value=action_value,
            )

            if action_id == "view_session":
                session_id = action_value
                return {
                    "response_type": "ephemeral",
                    "text": f"📋 *Session Information*\n"
                    f"Session ID: `{session_id}`\n\n"
                    f"💡 You can continue this conversation by:\n"
                    f"• Using `/agent [your message]` in Slack\n"
                    f"• Mentioning @{payload.user.name} in your message\n"
                    f"• Sending a DM to this bot\n\n"
                    f"Your session context will be maintained automatically!",
                }

            elif action_id == "ask_followup":
                session_id = action_value.replace("followup:", "")

                # For modals, we need to use the Slack Web API, not a direct response
                # Return a simple acknowledgment for now and log the attempt
                logger.info(
                    "Ask Follow-up button clicked",
                    user_id=payload.user.id,
                    session_id=session_id,
                    trigger_id=payload.trigger_id,
                )

                # TODO: Implement modal opening via Slack Web API
                return {
                    "response_type": "ephemeral",
                    "text": f"💬 *Follow-up Modal Coming Soon!*\n\n"
                    f"Session ID: `{session_id}`\n\n"
                    f"For now, you can continue this conversation by:\n"
                    f"• Using `/agent [your question]`\n"
                    f"• Sending me a direct message\n"
                    f"• Mentioning me in a channel\n\n"
                    f"I'll remember our previous conversation!",
                }

            return {"text": "Unknown action"}

        except Exception as e:
            logger.error("Error handling button interaction", error=str(e))
            return {"text": "❌ Error processing interaction"}

    async def handle_modal_submission(self, payload: SlackInteractionPayload) -> Dict:
        """Handle modal form submissions."""
        try:
            callback_id = payload.view.get("callback_id", "")

            if callback_id.startswith("followup_modal:"):
                session_id = callback_id.replace("followup_modal:", "")

                # Extract the user's input from the modal
                values = payload.view.get("state", {}).get("values", {})
                followup_input = (
                    values.get("followup_input_block", {})
                    .get("followup_input", {})
                    .get("value", "")
                )

                if not followup_input.strip():
                    return {
                        "response_action": "errors",
                        "errors": {
                            "followup_input_block": "Please enter your follow-up question"
                        },
                    }

                logger.info(
                    "Processing follow-up from modal",
                    user_id=payload.user.id,
                    session_id=session_id,
                    text=followup_input[:100],
                )

                # Forward to Request Manager with session context
                await self._forward_to_request_manager(
                    user_id=payload.user.id,
                    content=followup_input,
                    integration_type="slack",
                    metadata={
                        "slack_channel": (
                            payload.channel.id if payload.channel else payload.user.id
                        ),
                        "source": "slack_followup_modal",
                        "session_id": session_id,  # Include session ID for continuity
                    },
                )

                return {"response_action": "clear"}

            return {"response_action": "clear"}

        except Exception as e:
            logger.error("Error handling modal submission", error=str(e))
            return {
                "response_action": "errors",
                "errors": {
                    "followup_input_block": "Sorry, there was an error processing your request. Please try again."
                },
            }

    def _clean_message_text(self, text: str) -> str:
        """Clean message text by removing bot mentions."""
        import re

        # Remove <@BOTID> mentions
        text = re.sub(r"<@[UW][A-Z0-9]+>", "", text)
        # Remove extra whitespace
        return text.strip()

    async def _forward_to_request_manager(
        self,
        user_id: str,
        content: str,
        integration_type: str,
        metadata: Optional[Dict] = None,
    ) -> None:
        """Forward request to Request Manager."""
        try:
            payload = {
                "user_id": user_id,
                "content": content,
                "integration_type": integration_type,
                "request_type": "slack_interaction",
                "metadata": metadata or {},
            }

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.request_manager_url}/api/v1/requests/generic",
                    json=payload,
                    timeout=30.0,
                )
                response.raise_for_status()

                logger.info(
                    "Request forwarded to Request Manager",
                    user_id=user_id,
                    status_code=response.status_code,
                )

        except Exception as e:
            logger.error(
                "Failed to forward request to Request Manager",
                error=str(e),
                user_id=user_id,
            )
            raise
