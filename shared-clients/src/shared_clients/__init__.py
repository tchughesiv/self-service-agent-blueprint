"""
Shared client libraries for the self-service agent blueprint.

This package provides reusable client classes for interacting with various
components of the self-service agent system, including the Request Manager,
Agent Service, and other services.
"""

from .request_manager_client import CLIChatClient, RequestManagerClient
from .service_client import (
    IntegrationDispatcherClient,
    ServiceClient,
    cleanup_service_clients,
    get_integration_dispatcher_client,
    get_request_manager_client,
    initialize_service_clients,
)

__all__ = [
    "RequestManagerClient",
    "CLIChatClient",
    "ServiceClient",
    "IntegrationDispatcherClient",
    "get_request_manager_client",
    "get_integration_dispatcher_client",
    "initialize_service_clients",
    "cleanup_service_clients",
]
