# Request Management Layer

## Overview

The Request Management Layer is a comprehensive system that sits between external integrations (Slack, Web, CLI, Tools) and the AI agent services. It provides session management, request normalization, and event-driven communication using OpenShift Serverless (Knative) and CloudEvents.

## Architecture

The system follows an event-driven architecture with the following components:

### 1. Request Manager (FastAPI Knative Service)
- **Normalizes** incoming requests from different integration types
- **Manages sessions** using PostgreSQL as a key-value store
- **Routes requests** to appropriate agents via CloudEvents
- **Tracks conversation state** and request history

### 2. Agent Service (CloudEvent-driven Knative Service)
- **Processes normalized requests** from the Request Manager
- **Integrates with Llama Stack** for agent interactions
- **Manages agent sessions** and conversation context
- **Publishes responses** back via CloudEvents

### 3. Event Infrastructure
- **Knative Broker** with Kafka backend for event routing
- **CloudEvents** for standardized message format
- **Triggers** for event filtering and routing

### 4. Database Layer
- Uses existing `llama_agents` PostgreSQL database
- **Session management** with conversation state
- **Request/response logging** for audit and analytics
- **Integration configuration** storage

## Key Features

### Multi-Integration Support
- **Slack**: Full thread and channel support with user context
- **Web**: Browser-based interactions with session management
- **CLI**: Command-line interface integration
- **Tools**: Automated requests from external systems (ServiceNow, HR, etc.)

### Session Management
- **Persistent sessions** across conversation turns
- **Integration-specific context** preservation
- **User context** and metadata tracking
- **Automatic session cleanup** for inactive sessions

### Event-Driven Architecture
- **Asynchronous processing** for high throughput
- **Loose coupling** between components
- **Scalable** with Knative auto-scaling
- **Fault-tolerant** with event replay capabilities

### Request Normalization
- **Unified format** for all integration types
- **Context extraction** from platform-specific data
- **Agent routing** based on content and context
- **Metadata preservation** for debugging and analytics

## Database Schema

### request_sessions
```sql
- session_id (UUID, Primary Key)
- user_id (String, Indexed)
- integration_type (Enum: slack, web, cli, tool)
- channel_id, thread_id (Optional, for Slack)
- current_agent_id (String, Optional)
- llama_stack_session_id (String, Optional)
- status (Enum: active, inactive, completed, error)
- conversation_context (JSON)
- integration_metadata (JSON)
- user_context (JSON)
- total_requests (Integer)
- created_at, updated_at, last_activity_at (Timestamps)
```

### request_logs
```sql
- request_id (UUID, Primary Key)
- session_id (UUID, Foreign Key)
- request_type (String)
- request_content (Text)
- normalized_request (JSON)
- response_content (Text, Optional)
- response_metadata (JSON)
- agent_id (String, Optional)
- processing_time_ms (Integer, Optional)
- cloudevent_id, cloudevent_type (String, Optional)
- created_at, completed_at (Timestamps)
```

## API Endpoints

### Session Management
```
POST /api/v1/sessions                 # Create session
GET  /api/v1/sessions/{session_id}    # Get session info
```

### Integration Endpoints
```
POST /api/v1/requests/slack           # Slack requests
POST /api/v1/requests/web             # Web requests  
POST /api/v1/requests/cli             # CLI requests
POST /api/v1/requests/tool            # Tool requests
POST /api/v1/requests/generic         # Generic requests
```

### Event Handling
```
POST /api/v1/events/cloudevents       # CloudEvent webhook
```

### Health & Monitoring
```
GET  /health                          # Health check
```

## CloudEvent Types

### Request Events
- `com.self-service-agent.request.created`: New request from integration
- `com.self-service-agent.request.processed`: Request completed
- `com.self-service-agent.request.failed`: Request processing failed

### Response Events
- `com.self-service-agent.response.created`: Agent response generated
- `com.self-service-agent.agent.response-ready`: Agent response ready for delivery
- `com.self-service-agent.response.delivered`: Response delivered to integration

### Session Events
- `com.self-service-agent.session.created`: New session created
- `com.self-service-agent.session.updated`: Session state updated
- `com.self-service-agent.session.ended`: Session ended

## Deployment

### Container Images
- `quay.io/ecosystem-appeng/self-service-agent-request-manager:0.1.0`
- `quay.io/ecosystem-appeng/self-service-agent-service:0.1.0`

### Knative Services
```yaml
# Request Manager Service
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: request-manager

# Agent Service  
apiVersion: serving.knative.dev/v1
kind: Service
metadata:
  name: agent-service
```

### Event Infrastructure
```yaml
# Knative Broker
apiVersion: eventing.knative.dev/v1
kind: Broker
metadata:
  name: self-service-agent-broker

# Event Triggers
apiVersion: eventing.knative.dev/v1
kind: Trigger
metadata:
  name: request-created-trigger
```

## Development Workflow

### Build Services
```bash
# Build all images
make build-all-images

# Build specific services
make build-request-mgr-image
make build-agent-service-image
```

### Install Dependencies
```bash
# Install all dependencies
make install-all

# Install specific services
make install-request-manager
make install-agent-service
```

### Run Tests
```bash
# Run all tests
make test-all

# Run specific tests
make test-request-manager
make test-agent-service
```

### Local Development
```bash
# Request Manager
cd request-manager
uv run python -m uvicorn request_manager.main:app --reload

# Agent Service
cd agent-service  
uv run python -m uvicorn agent_service.main:app --reload
```

## Service Mesh Integration

The Request Management Layer is fully integrated with OpenShift Service Mesh (Istio) for advanced traffic management, security, and observability.

### Gateway Configuration

```yaml
apiVersion: networking.istio.io/v1beta1
kind: Gateway
metadata:
  name: self-service-agent-gateway
spec:
  selector:
    istio: ingressgateway
  servers:
  - port:
      number: 443
      name: https
      protocol: HTTPS
    hosts:
    - "api.selfservice.apps.cluster.local"
```

### Security Policies

- **JWT Authentication**: Web and CLI requests require valid JWT tokens
- **Slack Signature Verification**: Webhook requests verified using Slack signing secret
- **API Key Authentication**: Tool integrations use API keys
- **mTLS**: Service-to-service communication encrypted with mutual TLS
- **Authorization Policies**: Fine-grained access control based on user context

### Traffic Management

- **Load Balancing**: Least request algorithm for optimal distribution
- **Circuit Breaker**: Automatic failure detection and recovery
- **Rate Limiting**: Per-user and per-integration rate limits
- **Timeout Configuration**: Request-specific timeout policies

### Observability

- **Distributed Tracing**: End-to-end request tracing with Jaeger
- **Metrics Collection**: Custom metrics for request processing
- **Access Logs**: Structured logging with correlation IDs
- **Health Monitoring**: Istio health checks and service mesh metrics

## Configuration

### Environment Variables

#### Request Manager
```bash
POSTGRES_HOST=pgvector
POSTGRES_PORT=5432
POSTGRES_DB=llama_agents
POSTGRES_USER=<from-secret>
POSTGRES_PASSWORD=<from-secret>
BROKER_URL=http://broker-ingress.knative-eventing.svc.cluster.local
LOG_LEVEL=INFO
```

#### Agent Service
```bash
LLAMA_STACK_URL=http://llamastack:8321
BROKER_URL=http://broker-ingress.knative-eventing.svc.cluster.local
DEFAULT_AGENT_ID=routing-agent
AGENT_TIMEOUT=120
```

## Monitoring & Observability

### Health Checks
- Both services expose `/health` endpoints
- Database connectivity checks
- Service dependency validation

### Logging
- Structured JSON logging with correlation IDs
- Request/response tracing across services
- Performance metrics and timing

### Metrics
- Request processing times
- Session creation/update rates
- Event publishing success/failure rates
- Database connection pool status

## Security

### Authentication
- OpenShift Service Mesh (Istio) handles external authentication via JWT
- Slack signature verification for webhook security
- API key authentication for tool integrations
- Inter-service communication uses cluster networking
- Database credentials stored in Kubernetes secrets

### Authorization
- User context preserved throughout request flow
- Integration-specific access controls
- Audit logging for all requests

### Network Security
- Services run in dedicated namespace
- Network policies restrict inter-service communication
- Non-root container execution

## Scaling

### Auto-scaling
- **Request Manager**: Scales based on HTTP request load
- **Agent Service**: Scales based on CloudEvent processing
- **Event System**: Kafka partitioning for parallel processing

### Performance
- Connection pooling for database efficiency
- Asynchronous processing throughout
- Event-driven architecture prevents blocking

## Example Usage

See `examples/request-management-demo.py` for comprehensive examples of:
- Creating sessions for different integrations
- Sending requests through various channels
- Handling responses and session management
- Health monitoring and error handling

## Migration from Direct Agent Access

The Request Management Layer is designed to be backward-compatible:

1. **Existing integrations** can migrate incrementally
2. **Session state** is preserved during migration
3. **API compatibility** maintained where possible
4. **Performance improvements** through event-driven architecture

## Future Enhancements

- **Multi-tenant support** with organization isolation
- **Advanced routing** with ML-based intent detection  
- **Real-time notifications** via WebSockets
- **Analytics dashboard** for conversation insights
- **Integration marketplace** for custom connectors
