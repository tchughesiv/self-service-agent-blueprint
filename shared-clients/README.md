# Shared Clients

This package provides reusable client libraries for interacting with the self-service agent blueprint components.

## Components

### RequestManagerClient

Base client for interacting with the Request Manager service. Provides methods for:

- Sending requests to various endpoints (web, cli, tool, generic)
- Getting conversations (filter by user_email, session_id, etc.)
- Managing HTTP connections

### CLIChatClient

CLI-specific chat client that extends RequestManagerClient with:

- Interactive chat loop functionality
- Session management
- Command context handling
- Debug output support

### ServiceClient

Base HTTP client for service-to-service communication with:

- HTTP/2 support for better performance
- Connection pooling and keepalive
- Compression support (gzip, deflate, br)
- Standard REST methods (GET, POST, PUT, DELETE)
- Streaming support for Server-Sent Events

### IntegrationDispatcherClient

Client for the Integration Dispatcher service with:

- Delivery request methods
- Integration-specific operations
- Default configuration for Integration Dispatcher URL

### LlamaStackStreamProcessor

Unified stream processor for LlamaStack streaming responses with:

- Token usage extraction
- Content streaming with callbacks
- Error handling
- Tool call processing

## Usage

### Basic Request Manager Client

```python
from shared_clients import RequestManagerClient

async def main():
    client = RequestManagerClient(
        request_manager_url="http://localhost:8080",
        user_id="user123"
    )

    response = await client.send_request(
        content="Hello, agent!",
        integration_type="CLI",
        endpoint="generic"
    )

    print(response)
    await client.close()
```

### Get conversations

No auth required (matches generic endpoint). Optional filters: `user_email`, `user_id`, `session_id`, `start_date`, `end_date`, `integration_type` (channel where conversation started), `integration_types` (list; sessions that used at least one of these channels, full conversation), `agent_id` (sessions that used this agent, full conversation), plus `limit`, `offset`, `include_messages`, `random`. Each session in the response has `integration_type` (channel where the conversation started) and `integration_types` (channels used in that session).

```python
from shared_clients import RequestManagerClient

async def main():
    client = RequestManagerClient(request_manager_url="http://localhost:8080")

    result = await client.get_conversations(user_email="user@example.com")
    print(result["sessions"])
    print(result["count"], result["total"])

    await client.close()
```

### CLI Chat Client

```python
from shared_clients import CLIChatClient

async def main():
    client = CLIChatClient(
        request_manager_url="http://localhost:8080"
    )

    # Run interactive chat
    await client.chat_loop(
        initial_message="please introduce yourself and tell me how you can help"
    )
```

### Programmatic Usage

```python
from shared_clients import CLIChatClient

async def main():
    client = CLIChatClient()

    # Send individual messages
    response1 = await client.send_message("Hello!")
    print(f"Agent: {response1}")

    response2 = await client.send_message("How can you help me?")
    print(f"Agent: {response2}")

    # Reset session
    await client.reset_session()

    await client.close()
```

### Service-to-Service Communication


For session management, use `shared-models.BaseSessionManager` directly for database access.

### Stream Processing

```python
from shared_clients import LlamaStackStreamProcessor

async def process_streaming_response(response_stream):
    result = await LlamaStackStreamProcessor.process_stream(
        response_stream=response_stream,
        on_content=lambda content: print(content, end="", flush=True),
        on_error=lambda error: print(f"Error: {error}"),
        collect_content=True
    )

    print(f"\nTotal tokens: {result['total_tokens']}")
    print(f"Full content: {result['content']}")
```

## Installation

This package is designed to be used as a local dependency within the self-service agent blueprint project.

```bash
# From the project root, install all dependencies
make install-all

# Or install just shared-clients
cd shared-clients
uv sync

# Install with dev dependencies
uv sync --group dev
```

## Environment Variables

### Request Manager Client
- `REQUEST_MANAGER_URL`: URL of the Request Manager service (default: `http://localhost:8080`)
- `AGENT_MESSAGE_TERMINATOR`: Message terminator for agent responses (optional)

### Service Clients
- `INTEGRATION_DISPATCHER_URL`: URL of the Integration Dispatcher (default: `http://self-service-agent-integration-dispatcher:80`)

## Development

### Run Tests
```bash
# From project root
make test-shared-clients

# Or directly
cd shared-clients
uv run pytest
```

### Format Code
```bash
# From project root
make format

# Or directly
cd shared-clients
uv run black .
uv run isort .
```

### Lint Code
```bash
# From project root
make lint

# Or directly
cd shared-clients
uv run flake8 .
uv run mypy src/
```

## Exported Classes and Functions

### Main Clients
- `RequestManagerClient` - Base client for Request Manager
- `CLIChatClient` - CLI chat interface
- `ServiceClient` - Base HTTP client
- `IntegrationDispatcherClient` - Integration Dispatcher client

### Helper Functions
- `initialize_service_clients()` - Initialize all service clients
- `cleanup_service_clients()` - Clean up all service clients
- `get_request_manager_client()` - Get Request Manager client instance
- `get_integration_dispatcher_client()` - Get Integration Dispatcher client instance

### Utilities
- `LlamaStackStreamProcessor` - Stream processing for LlamaStack responses
