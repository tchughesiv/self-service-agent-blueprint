# Port-Forward Quick Start

Quick reference for accessing Self-Service Agent services via port-forwarding.

## TL;DR - One Command Setup

```bash
# Set your namespace
export NAMESPACE=your-namespace

# Start all port-forwards (run each in a separate terminal)
kubectl port-forward svc/self-service-agent-request-manager-00001-private 8080:80 -n $NAMESPACE &
kubectl port-forward svc/self-service-agent-agent-service-00001-private 8081:80 -n $NAMESPACE &
kubectl port-forward deployment/self-service-agent-asset-manager 8082:8080 -n $NAMESPACE &
kubectl port-forward svc/self-service-agent-integration-dispatcher-00001-private 8083:80 -n $NAMESPACE &

# Test everything is working
curl http://localhost:8080/health && echo " ✓ Request Manager"
curl http://localhost:8081/health && echo " ✓ Agent Service"
curl http://localhost:8082/health && echo " ✓ Asset Manager"
curl http://localhost:8083/health && echo " ✓ Integration Dispatcher"
```

## Service Map

| Service | Local URL | Purpose |
|---------|-----------|---------|
| Request Manager | http://localhost:8080 | Main API, handles all request types |
| Agent Service | http://localhost:8081 | AI processing, CloudEvent handling |
| Asset Manager | http://localhost:8082 | Agent and knowledge base management |
| Integration Dispatcher | http://localhost:8083 | Delivery management (Slack, email, etc.) |

## Quick Tests

```bash
# Basic request
curl -X POST http://localhost:8080/api/v1/requests/web \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "content": "Hello world!"}'

# List agents
curl http://localhost:8082/agents

# Check integrations
curl http://localhost:8083/api/v1/users/test-user/integrations
```

## Troubleshooting

**Port-forward fails?** Use the `-private` services:
```bash
# ❌ This fails (no selectors)
kubectl port-forward svc/self-service-agent-request-manager-00001 8080:80 -n $NAMESPACE

# ✅ This works (has selectors)
kubectl port-forward svc/self-service-agent-request-manager-00001-private 8080:80 -n $NAMESPACE
```

**Service not found?** Check what's available:
```bash
kubectl get svc -n $NAMESPACE | grep self-service-agent
kubectl get ksvc -n $NAMESPACE
```

## Background Process Script

Save as `start-port-forwards.sh`:

```bash
#!/bin/bash
NAMESPACE=${1:-tommy}

echo "Starting port-forwards for namespace: $NAMESPACE"

kubectl port-forward svc/self-service-agent-request-manager-00001-private 8080:80 -n $NAMESPACE &
kubectl port-forward svc/self-service-agent-agent-service-00001-private 8081:80 -n $NAMESPACE &
kubectl port-forward deployment/self-service-agent-asset-manager 8082:8080 -n $NAMESPACE &
kubectl port-forward svc/self-service-agent-integration-dispatcher-00001-private 8083:80 -n $NAMESPACE &

echo "Port-forwards started. URLs:"
echo "  Request Manager: http://localhost:8080"
echo "  Agent Service: http://localhost:8081"
echo "  Asset Manager: http://localhost:8082"
echo "  Integration Dispatcher: http://localhost:8083"

echo "Press Ctrl+C to stop all port-forwards"
wait
```

Usage:
```bash
chmod +x start-port-forwards.sh
./start-port-forwards.sh your-namespace
```

For detailed testing procedures, see [PORT_FORWARD_TESTING.md](PORT_FORWARD_TESTING.md).
