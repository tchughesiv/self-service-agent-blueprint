"""Integration defaults service for user integration configurations."""

import os
from typing import Any, Dict, List, Optional

import structlog
from shared_models.models import IntegrationType

logger = structlog.get_logger()


class IntegrationDefaultsService:
    """Service for managing integration defaults with user overrides."""

    def __init__(self):
        """Initialize integration defaults service."""
        self.default_integrations = self._load_default_integrations()

        logger.info(
            "Integration defaults service initialized",
            default_integrations=list(self.default_integrations.keys()),
        )

    def _load_default_integrations(self) -> Dict[str, Dict[str, Any]]:
        """Load default integration configurations from environment."""
        # Default configurations - will be updated with health check results
        defaults = {
            "SLACK": {
                "enabled": False,  # Will be updated by health check
                "priority": 1,
                "retry_count": 3,
                "retry_delay_seconds": 60,
                "config": {
                    "thread_replies": False,
                    "mention_user": False,
                    "include_agent_info": True,
                },
            },
            "EMAIL": {
                "enabled": False,  # Will be updated by health check
                "priority": 2,
                "retry_count": 3,
                "retry_delay_seconds": 60,
                "config": {"include_agent_info": True},
            },
            "WEBHOOK": {
                "enabled": False,  # Always disabled by default
                "priority": 3,
                "retry_count": 1,
                "retry_delay_seconds": 30,
                "config": {},
            },
            "SMS": {
                "enabled": False,  # Always disabled by default
                "priority": 4,
                "retry_count": 2,
                "retry_delay_seconds": 45,
                "config": {},
            },
            "TEST": {
                "enabled": False,  # Will be updated by health check
                "priority": 5,
                "retry_count": 1,
                "retry_delay_seconds": 10,
                "config": {},
            },
        }

        # Override with environment variables if available
        for integration_type in defaults:
            env_prefix = f"INTEGRATION_DEFAULTS_{integration_type}_"

            # Check for enabled override
            enabled_env = os.getenv(f"{env_prefix}ENABLED")
            if enabled_env is not None:
                defaults[integration_type]["enabled"] = enabled_env.lower() == "true"

            # Check for priority override
            priority_env = os.getenv(f"{env_prefix}PRIORITY")
            if priority_env is not None:
                try:
                    defaults[integration_type]["priority"] = int(priority_env)
                except ValueError:
                    logger.warning(
                        "Invalid priority value for integration defaults",
                        integration_type=integration_type,
                        value=priority_env,
                    )

        return defaults

    async def get_user_integrations(
        self, user_id: str, user_overrides: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Get integration configurations for a user using integration defaults with overrides.

        Args:
            user_id: User identifier
            user_overrides: Optional user-specific overrides

        Returns:
            List of integration configurations
        """
        # Update integration health status before returning defaults
        await self._update_integration_health()

        # Start with default configurations
        integrations = []

        for integration_type, default_config in self.default_integrations.items():
            # Create integration config from defaults
            config = {
                "user_id": user_id,
                "integration_type": IntegrationType(integration_type),
                "enabled": default_config["enabled"],
                "priority": default_config["priority"],
                "retry_count": default_config["retry_count"],
                "retry_delay_seconds": default_config["retry_delay_seconds"],
                "config": default_config["config"].copy(),
            }

            # Apply user overrides if provided
            if user_overrides:
                user_override = user_overrides.get(integration_type)
                if user_override:
                    # Apply overrides
                    if "enabled" in user_override:
                        config["enabled"] = user_override["enabled"]
                    if "priority" in user_override:
                        config["priority"] = user_override["priority"]
                    if "retry_count" in user_override:
                        config["retry_count"] = user_override["retry_count"]
                    if "retry_delay_seconds" in user_override:
                        config["retry_delay_seconds"] = user_override[
                            "retry_delay_seconds"
                        ]
                    if "config" in user_override:
                        # Merge config dictionaries
                        config["config"].update(user_override["config"])

            # Only include enabled integrations
            if config["enabled"]:
                integrations.append(config)

        logger.info(
            "Generated user integrations with integration defaults",
            user_id=user_id,
            integrations_count=len(integrations),
            integration_types=[i["integration_type"] for i in integrations],
        )

        return integrations

    def get_default_integrations(self) -> Dict[str, Dict[str, Any]]:
        """Get the current default integration configurations."""
        return self.default_integrations.copy()

    def update_default_integration(
        self, integration_type: str, config: Dict[str, Any]
    ) -> None:
        """Update default configuration for an integration type.

        Args:
            integration_type: Integration type to update
            config: New configuration
        """
        if integration_type in self.default_integrations:
            self.default_integrations[integration_type].update(config)
            logger.info(
                "Updated default integration configuration",
                integration_type=integration_type,
                config=config,
            )
        else:
            logger.warning(
                "Unknown integration type for default update",
                integration_type=integration_type,
            )

    def is_integration_enabled_by_default(self, integration_type: str) -> bool:
        """Check if an integration is enabled by default.

        Args:
            integration_type: Integration type to check

        Returns:
            True if enabled by default, False otherwise
        """
        return self.default_integrations.get(integration_type, {}).get("enabled", False)

    async def _update_integration_health(self) -> None:
        """Update integration enabled status based on health checks."""
        # Import here to avoid circular imports
        from .email import EmailIntegrationHandler
        from .slack import SlackIntegrationHandler
        from .test import TestIntegrationHandler

        # Check Slack health
        try:
            slack_handler = SlackIntegrationHandler()
            slack_healthy = await slack_handler.health_check()
            self.default_integrations["SLACK"]["enabled"] = slack_healthy

            logger.info("Slack integration health check", healthy=slack_healthy)
        except Exception as e:
            logger.warning("Slack integration health check failed", error=str(e))
            self.default_integrations["SLACK"]["enabled"] = False

        # Check Email health
        try:
            email_handler = EmailIntegrationHandler()
            email_healthy = await email_handler.health_check()
            self.default_integrations["EMAIL"]["enabled"] = email_healthy

            logger.info("Email integration health check", healthy=email_healthy)
        except Exception as e:
            logger.warning("Email integration health check failed", error=str(e))
            self.default_integrations["EMAIL"]["enabled"] = False

        # Check Test health
        try:
            test_handler = TestIntegrationHandler()
            test_healthy = await test_handler.health_check()
            self.default_integrations["TEST"]["enabled"] = test_healthy

            logger.info("Test integration health check", healthy=test_healthy)
        except Exception as e:
            logger.warning("Test integration health check failed", error=str(e))
            self.default_integrations["TEST"]["enabled"] = False
