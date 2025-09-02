# Getting Started with Port-Forward Access

This comprehensive guide demonstrates how to access and test your Self-Service Agent services using `kubectl port-forward` instead of external domains. This approach is ideal for development, testing, and environments where external ingress is not available or desired.

## Quick Start Options

- **[PORT_FORWARD_QUICK_START.md](PORT_FORWARD_QUICK_START.md)** - TL;DR version with one-command setup
- **[PORT_FORWARD_TESTING.md](PORT_FORWARD_TESTING.md)** - Comprehensive testing procedures and automation
- **This guide** - Complete setup and usage documentation

## Why Use Port-Forward?

- ✅ **No external DNS required** - Works with any Kubernetes cluster
- ✅ **Bypasses ingress issues** - Direct access to services
- ✅ **Development friendly** - Easy local testing
- ✅ **Works with Knative cluster-local** - Perfect for your current setup
- ✅ **Secure** - Traffic stays within your local machine and cluster

## Prerequisites

- `kubectl` configured to access your cluster
- Services deployed via Helm (see main README.md)
- Access to the namespace where services are deployed

## Service Overview

Your deployment creates several services with different access patterns:

| Service | Purpose | Port-Forward Target | Local Port |
|---------|---------|-------------------|------------|
| Request Manager | Main API, handles all request types | `svc/self-service-agent-request-manager-00001-private` | 8080 |
| Agent Service | CloudEvent processing, AI interactions | `svc/self-service-agent-agent-service-00001-private` | 8081 |
| Asset Manager | Agent/KB management | `deployment/self-service-agent-asset-manager` | 8082 |
| Integration Dispatcher | Delivery management (Slack, email, etc.) | `svc/self-service-agent-integration-dispatcher-00001-private` | 8083 |
| PostgreSQL | Database | `svc/pgvector` | 5432 |

## Quick Start

### 1. Set Up Port-Forwarding

Open separate terminal windows and run these commands (replace `your-namespace` with your actual namespace):

```bash
# Terminal 1: Request Manager (Main API)
kubectl port-forward svc/self-service-agent-request-manager-00001-private 8080:80 -n your-namespace

# Terminal 2: Agent Service  
kubectl port-forward svc/self-service-agent-agent-service-00001-private 8081:80 -n your-namespace

# Terminal 3: Asset Manager
kubectl port-forward deployment/self-service-agent-asset-manager 8082:8080 -n your-namespace

# Terminal 4: Integration Dispatcher
kubectl port-forward svc/self-service-agent-integration-dispatcher-00001-private 8083:80 -n your-namespace
```

### 2. Verify Services Are Running

Test each service's health endpoint:

```bash
# Request Manager
curl http://localhost:8080/health
# Expected: {"status": "healthy", "service": "request-manager"}

# Agent Service  
curl http://localhost:8081/health
# Expected: {"status": "healthy", "service": "agent-service"}

# Asset Manager
curl http://localhost:8082/health  
# Expected: {"status": "healthy", "service": "asset-manager"}

# Integration Dispatcher
curl http://localhost:8083/health
# Expected: {"status": "healthy", "service": "integration-dispatcher"}
```

## Testing the Request Management API

### Basic Request (No Authentication)

```bash
# Simple health check request
curl -X POST http://localhost:8080/api/v1/requests/web \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user",
    "content": "Hello! Can you help me with a test request?"
  }'
```

### Tool Integration Request

```bash
# Tool request (requires API key configuration)
curl -X POST http://localhost:8080/api/v1/requests/tool \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "user_id": "system",
    "content": "Automated request for testing",
    "tool_id": "snow-integration",
    "trigger_event": "test.event"
  }'
```

### Session Management

```bash
# Create a session
curl -X POST http://localhost:8080/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "demo-user",
    "integration_type": "web",
    "metadata": {
      "client": "port-forward-test"
    }
  }'

# Get session (replace SESSION_ID with returned ID)
curl http://localhost:8080/api/v1/sessions/SESSION_ID
```

## Testing Integration Management

With the Integration Dispatcher port-forwarded to 8083:

```bash
# Configure user integrations
curl -X POST http://localhost:8083/api/v1/users/demo-user/integrations \
  -H "Content-Type: application/json" \
  -d '{
    "integration_type": "slack",
    "enabled": true,
    "config": {
      "channel_id": "C1234567890",
      "thread_replies": true
    }
  }'

# Get user's delivery history
curl http://localhost:8083/api/v1/users/demo-user/deliveries
```

## Asset Management

With the Asset Manager port-forwarded to 8082:

```bash
# List available agents
curl http://localhost:8082/agents

# List knowledge bases  
curl http://localhost:8082/knowledge_bases

# Register new assets (requires asset-manager directory)
cd asset-manager && uv run script/register_assets.py
```

## Database Access

If you need direct database access:

```bash
# Terminal 5: PostgreSQL
kubectl port-forward svc/pgvector 5432:5432 -n your-namespace

# Connect with psql (in another terminal)
psql -h localhost -p 5432 -U llama_user -d llama_agents
```

## Troubleshooting

### Port-Forward Connection Issues

**Problem**: `error: cannot attach to *v1.Service: invalid service 'service-name': Service is defined without a selector`

**Solution**: Use the `-private` services for Knative services:
```bash
# ❌ Wrong - Main Knative service (no selectors)
kubectl port-forward svc/self-service-agent-request-manager-00001 8080:80 -n your-namespace

# ✅ Correct - Private service (has selectors)  
kubectl port-forward svc/self-service-agent-request-manager-00001-private 8080:80 -n your-namespace
```

### Service Not Ready

**Problem**: Connection refused or service not responding

**Solution**: Check service status:
```bash
# Check Knative services
kubectl get ksvc -n your-namespace

# Check pods
kubectl get pods -n your-namespace

# Check service endpoints
kubectl get endpoints -n your-namespace
```

### Finding the Correct Service Names

List all services in your namespace:
```bash
kubectl get svc -n your-namespace | grep self-service-agent
```

Look for services ending in `-00001-private` for Knative services, or use deployment names for regular deployments.

## Advanced Usage

### Background Port-Forwarding

Run port-forwards in the background:

```bash
# Start all port-forwards in background
kubectl port-forward svc/self-service-agent-request-manager-00001-private 8080:80 -n your-namespace &
kubectl port-forward svc/self-service-agent-agent-service-00001-private 8081:80 -n your-namespace &
kubectl port-forward deployment/self-service-agent-asset-manager 8082:8080 -n your-namespace &
kubectl port-forward svc/self-service-agent-integration-dispatcher-00001-private 8083:80 -n your-namespace &

# List background jobs
jobs

# Kill all background port-forwards
kill %1 %2 %3 %4
```

### Port-Forward Script

Create a helper script `port-forward.sh`:

```bash
#!/bin/bash
NAMESPACE=${1:-tommy}

echo "Setting up port-forwards for namespace: $NAMESPACE"

kubectl port-forward svc/self-service-agent-request-manager-00001-private 8080:80 -n $NAMESPACE &
kubectl port-forward svc/self-service-agent-agent-service-00001-private 8081:80 -n $NAMESPACE &
kubectl port-forward deployment/self-service-agent-asset-manager 8082:8080 -n $NAMESPACE &
kubectl port-forward svc/self-service-agent-integration-dispatcher-00001-private 8083:80 -n $NAMESPACE &

echo "Port-forwards started:"
echo "  Request Manager: http://localhost:8080"
echo "  Agent Service: http://localhost:8081"  
echo "  Asset Manager: http://localhost:8082"
echo "  Integration Dispatcher: http://localhost:8083"

echo "Press Ctrl+C to stop all port-forwards"
wait
```

Usage:
```bash
chmod +x port-forward.sh
./port-forward.sh your-namespace
```

## Production Considerations

While port-forwarding is excellent for development, consider these alternatives for production:

1. **Service Mesh Gateway** - Use the configured Istio gateway for external access
2. **OpenShift Routes** - Create routes for external access  
3. **Ingress Controllers** - Configure ingress for external access
4. **VPN Access** - Access cluster-local services via VPN

For production setup, see [DEPLOYMENT_SERVICE_MESH.md](DEPLOYMENT_SERVICE_MESH.md).

## Next Steps

- Explore the [Request Management documentation](REQUEST_MANAGEMENT.md)
- Set up [Service Mesh deployment](DEPLOYMENT_SERVICE_MESH.md) for production
- Review [Knative configuration options](KNATIVE_CONFIGURATION.md)
- Check out [integration examples](examples/helm-deployment-examples.md)
