"""Centralized HTTP client for service-to-service communication."""

import os
from typing import Any, Dict, Optional

import httpx
import structlog

logger = structlog.get_logger()


class ServiceClient:
    """Centralized HTTP client for service-to-service communication."""

    def __init__(self, base_url: str, timeout: float = 30.0, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.client = httpx.AsyncClient(
            timeout=timeout,
            verify=verify_ssl,
            follow_redirects=True,
            # Performance optimizations
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            http2=True,  # Enable HTTP/2 for better performance
            headers={"Accept-Encoding": "gzip, deflate, br"},  # Enable compression
        )

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """Make a GET request."""
        url = f"{self.base_url}{path}"
        logger.debug("Making GET request", url=url)
        return await self.client.get(url, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """Make a POST request."""
        url = f"{self.base_url}{path}"
        logger.debug("Making POST request", url=url)
        return await self.client.post(url, **kwargs)

    async def stream_post(self, path: str, **kwargs: Any) -> Any:
        """Make a streaming POST request for Server-Sent Events."""
        url = f"{self.base_url}{path}"
        logger.debug("Making streaming POST request", url=url)
        # Return the async context manager directly
        return self.client.stream("POST", url, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        """Make a PUT request."""
        url = f"{self.base_url}{path}"
        logger.debug("Making PUT request", url=url)
        return await self.client.put(url, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """Make a DELETE request."""
        url = f"{self.base_url}{path}"
        logger.debug("Making DELETE request", url=url)
        return await self.client.delete(url, **kwargs)

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()


class RequestManagerClient(ServiceClient):
    """Client for communicating with the Request Manager."""

    def __init__(self, base_url: Optional[str] = None, **kwargs: Any) -> None:
        url = (
            base_url
            or os.getenv(
                "REQUEST_MANAGER_URL", "http://self-service-agent-request-manager:80"
            )
            or "http://self-service-agent-request-manager:80"
        )
        super().__init__(url, **kwargs)

    async def send_web_request(
        self, request_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Send a web request."""
        try:
            response = await self.post("/api/v1/requests/web", json=request_data)
            response.raise_for_status()
            result = response.json()
            return result if isinstance(result, dict) else None
        except Exception as e:
            logger.error("Failed to send web request", error=str(e))
            return None

    async def send_cli_request(
        self, request_data: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Send a CLI request."""
        try:
            response = await self.post("/api/v1/requests/cli", json=request_data)
            response.raise_for_status()
            result = response.json()
            return result if isinstance(result, dict) else None
        except Exception as e:
            logger.error("Failed to send CLI request", error=str(e))
            return None


class IntegrationDispatcherClient(ServiceClient):
    """Client for communicating with the Integration Dispatcher."""

    def __init__(self, base_url: Optional[str] = None, **kwargs: Any) -> None:
        url = (
            base_url
            or os.getenv(
                "INTEGRATION_DISPATCHER_URL",
                "http://self-service-agent-integration-dispatcher:8080",
            )
            or "http://self-service-agent-integration-dispatcher:8080"
        )
        super().__init__(url, **kwargs)

    async def deliver_response(self, delivery_data: Dict[str, Any]) -> bool:
        """Deliver a response to the integration dispatcher."""
        try:
            # Handle both Pydantic models and dictionaries
            if hasattr(delivery_data, "model_dump"):
                json_data = delivery_data.model_dump(mode="json")
            else:
                json_data = delivery_data
            response = await self.post("/deliver", json=json_data)
            response.raise_for_status()
            return True
        except Exception as e:
            logger.error("Failed to deliver response", error=str(e))
            return False


# Global client instances
_request_manager_client: Optional[RequestManagerClient] = None
_integration_dispatcher_client: Optional[IntegrationDispatcherClient] = None


def get_request_manager_client() -> Optional[RequestManagerClient]:
    """Get the global request manager client."""
    return _request_manager_client


def get_integration_dispatcher_client() -> Optional[IntegrationDispatcherClient]:
    """Get the global integration dispatcher client."""
    return _integration_dispatcher_client


def initialize_service_clients(
    request_manager_url: Optional[str] = None,
    integration_dispatcher_url: Optional[str] = None,
    integration_timeout: float = 30.0,
) -> None:
    """Initialize the global service client instances."""
    global _request_manager_client, _integration_dispatcher_client

    _request_manager_client = RequestManagerClient(
        base_url=request_manager_url, timeout=integration_timeout
    )
    _integration_dispatcher_client = IntegrationDispatcherClient(
        base_url=integration_dispatcher_url, timeout=integration_timeout
    )

    logger.debug("Initialized service clients")


async def cleanup_service_clients() -> None:
    """Clean up the global service client instances."""
    global _request_manager_client, _integration_dispatcher_client

    if _request_manager_client:
        await _request_manager_client.close()
        _request_manager_client = None

    if _integration_dispatcher_client:
        await _integration_dispatcher_client.close()
        _integration_dispatcher_client = None

    logger.info("Cleaned up service clients")
