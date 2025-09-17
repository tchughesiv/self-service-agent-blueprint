"""Shared health check utilities for all services."""

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


class HealthCheckResult:
    """Health check result with detailed status information."""

    def __init__(
        self,
        status: str = "healthy",
        service_name: str = "unknown",
        version: str = "0.1.0",
        database_connected: bool = False,
        integrations_available: List[str] = None,
        integration_errors: Dict[str, str] = None,
        services: Dict[str, str] = None,
    ):
        self.status = status
        self.service_name = service_name
        self.version = version
        self.database_connected = database_connected
        self.integrations_available = integrations_available or []
        self.integration_errors = integration_errors or {}
        self.services = services or {}
        self.timestamp = datetime.now(timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON response."""
        return {
            "status": self.status,
            "service": self.service_name,
            "version": self.version,
            "timestamp": self.timestamp.isoformat(),
            "database_connected": self.database_connected,
            "integrations_available": self.integrations_available,
            "integration_errors": self.integration_errors,
            "services": self.services,
        }


class HealthChecker:
    """Shared health check utility for all services."""

    def __init__(self, service_name: str, version: str = "0.1.0"):
        self.service_name = service_name
        self.version = version

    async def check_database(self, db: AsyncSession) -> bool:
        """Check database connectivity."""
        try:
            await db.execute(text("SELECT 1"))
            logger.debug("Database health check passed")
            return True
        except Exception as e:
            logger.error("Database health check failed", error=str(e))
            return False

    async def check_integrations(
        self, integration_handlers: Dict[str, Any] = None
    ) -> tuple[List[str], Dict[str, str]]:
        """Check integration handlers health."""
        if not integration_handlers:
            return [], {}

        integrations_available = []
        integration_errors = {}

        for integration_name, handler in integration_handlers.items():
            try:
                if hasattr(handler, "health_check"):
                    is_healthy = await handler.health_check()
                    if is_healthy:
                        integrations_available.append(integration_name)
                        logger.debug(
                            "Integration health check passed",
                            integration=integration_name,
                        )
                    else:
                        integration_errors[integration_name] = "Health check failed"
                        logger.warning(
                            "Integration health check failed",
                            integration=integration_name,
                        )
                else:
                    # If no health check method, assume healthy
                    integrations_available.append(integration_name)
            except Exception as e:
                integration_errors[integration_name] = str(e)
                logger.error(
                    "Integration health check error",
                    integration=integration_name,
                    error=str(e),
                )

        return integrations_available, integration_errors

    async def perform_health_check(
        self,
        db: Optional[AsyncSession] = None,
        integration_handlers: Dict[str, Any] = None,
        additional_checks: Dict[str, callable] = None,
    ) -> HealthCheckResult:
        """Perform comprehensive health check."""
        logger.debug("Starting health check", service=self.service_name)

        # Check database
        database_connected = False
        if db:
            database_connected = await self.check_database(db)

        # Check integrations
        integrations_available, integration_errors = await self.check_integrations(
            integration_handlers
        )

        # Run additional custom checks
        services = {}
        if additional_checks:
            for service_name, check_func in additional_checks.items():
                try:
                    result = await check_func()
                    services[service_name] = "healthy" if result else "unhealthy"
                except Exception as e:
                    services[service_name] = f"error: {str(e)}"
                    logger.error(
                        "Additional health check failed",
                        service=service_name,
                        error=str(e),
                    )

        # Determine overall status
        status = "healthy"
        if not database_connected:
            status = "degraded"
        if integration_errors:
            status = "degraded"

        result = HealthCheckResult(
            status=status,
            service_name=self.service_name,
            version=self.version,
            database_connected=database_connected,
            integrations_available=integrations_available,
            integration_errors=integration_errors,
            services=services,
        )

        logger.debug(
            "Health check completed",
            service=self.service_name,
            status=status,
            database_connected=database_connected,
            integrations_count=len(integrations_available),
            errors_count=len(integration_errors),
        )

        return result


# Convenience function for simple health checks
async def simple_health_check(
    service_name: str,
    version: str = "0.1.0",
    db: Optional[AsyncSession] = None,
) -> Dict[str, Any]:
    """Simple health check for basic services."""
    checker = HealthChecker(service_name, version)
    result = await checker.perform_health_check(db=db)
    return result.to_dict()
