# Port-Forward Testing Guide

This guide provides comprehensive testing procedures using `kubectl port-forward` for the Self-Service Agent services. This approach is ideal when external ingress is not available or for development/testing scenarios.

## Overview

Port-forwarding allows you to access Kubernetes services directly from your local machine without requiring external DNS, ingress controllers, or load balancers. This is particularly useful for:

- **Development environments** where external access isn't configured
- **Testing scenarios** where you need direct service access
- **Knative cluster-local services** that don't have external routes
- **Troubleshooting** service connectivity issues

## Quick Setup

### 1. Identify Your Services

First, check what services are available in your namespace:

```bash
# Replace 'your-namespace' with your actual namespace
export NAMESPACE=your-namespace

# List all services
kubectl get svc -n $NAMESPACE | grep self-service-agent

# List Knative services specifically  
kubectl get ksvc -n $NAMESPACE
```

### 2. Start Port-Forwarding Sessions

Open multiple terminal windows and run these commands:

```bash
# Terminal 1: Request Manager (Main API)
kubectl port-forward svc/self-service-agent-request-manager-00001-private 8080:80 -n $NAMESPACE

# Terminal 2: Agent Service (AI Processing)
kubectl port-forward svc/self-service-agent-agent-service-00001-private 8081:80 -n $NAMESPACE

# Terminal 3: Asset Manager (Agent/KB Management)
kubectl port-forward deployment/self-service-agent-asset-manager 8082:8080 -n $NAMESPACE

# Terminal 4: Integration Dispatcher (Delivery Management)
kubectl port-forward svc/self-service-agent-integration-dispatcher-00001-private 8083:80 -n $NAMESPACE

# Terminal 5: Database (Optional)
kubectl port-forward svc/pgvector 5432:5432 -n $NAMESPACE
```

## Testing Procedures

### Health Check Testing

Verify all services are responding:

```bash
# Test all health endpoints
echo "Testing Request Manager..."
curl -s http://localhost:8080/health | jq .

echo "Testing Agent Service..."
curl -s http://localhost:8081/health | jq .

echo "Testing Asset Manager..."
curl -s http://localhost:8082/health | jq .

echo "Testing Integration Dispatcher..."
curl -s http://localhost:8083/health | jq .
```

Expected responses should include service status and identification.

### Request Management API Testing

#### Basic Web Request
```bash
curl -X POST http://localhost:8080/api/v1/requests/web \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user",
    "content": "Hello! This is a test request via port-forward.",
    "session_id": "test-session-001"
  }' | jq .
```

#### Tool Integration Request
```bash
curl -X POST http://localhost:8080/api/v1/requests/tool \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-test-api-key" \
  -d '{
    "user_id": "system",
    "content": "Automated test request",
    "tool_id": "test-tool",
    "trigger_event": "port-forward.test"
  }' | jq .
```

#### Session Management
```bash
# Create a new session
SESSION_RESPONSE=$(curl -s -X POST http://localhost:8080/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test-user",
    "integration_type": "web",
    "metadata": {
      "test_type": "port-forward",
      "client": "curl"
    }
  }')

echo "Session created: $SESSION_RESPONSE"

# Extract session ID (assuming jq is available)
SESSION_ID=$(echo $SESSION_RESPONSE | jq -r '.session_id // .id')

# Get session details
curl -s http://localhost:8080/api/v1/sessions/$SESSION_ID | jq .
```

### Agent Service Testing

Test the CloudEvent processing capabilities:

```bash
# Send a CloudEvent to the agent service
curl -X POST http://localhost:8081/ \
  -H "Content-Type: application/cloudevents+json" \
  -H "ce-specversion: 1.0" \
  -H "ce-type: com.self-service-agent.request.created" \
  -H "ce-source: port-forward-test" \
  -H "ce-id: test-$(date +%s)" \
  -H "ce-time: $(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -d '{
    "user_id": "test-user",
    "content": "Test request for agent processing",
    "request_id": "pf-test-001"
  }' | jq .
```

### Asset Manager Testing

Test agent and knowledge base management:

```bash
# List available agents
curl -s http://localhost:8082/agents | jq .

# List knowledge bases
curl -s http://localhost:8082/knowledge_bases | jq .

# Get agent details (replace 'agent-id' with actual ID)
curl -s http://localhost:8082/agents/routing-agent | jq .

# Health check with detailed info
curl -s http://localhost:8082/health | jq .
```

### Integration Dispatcher Testing

Test delivery and integration management:

```bash
# Configure user integrations
curl -X POST http://localhost:8083/api/v1/users/test-user/integrations \
  -H "Content-Type: application/json" \
  -d '{
    "integration_type": "slack",
    "enabled": true,
    "config": {
      "channel_id": "C1234567890",
      "thread_replies": true,
      "test_mode": true
    }
  }' | jq .

# Get user integrations
curl -s http://localhost:8083/api/v1/users/test-user/integrations | jq .

# Check delivery history
curl -s http://localhost:8083/api/v1/users/test-user/deliveries | jq .
```

## Advanced Testing Scenarios

### End-to-End Request Flow

Test the complete request processing flow:

```bash
#!/bin/bash
# E2E test script

echo "=== End-to-End Request Flow Test ==="

# Step 1: Create a session
echo "1. Creating session..."
SESSION_RESPONSE=$(curl -s -X POST http://localhost:8080/api/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "e2e-test-user",
    "integration_type": "web"
  }')

SESSION_ID=$(echo $SESSION_RESPONSE | jq -r '.session_id // .id')
echo "Session ID: $SESSION_ID"

# Step 2: Submit a request
echo "2. Submitting request..."
REQUEST_RESPONSE=$(curl -s -X POST http://localhost:8080/api/v1/requests/web \
  -H "Content-Type: application/json" \
  -d "{
    \"user_id\": \"e2e-test-user\",
    \"content\": \"Please help me understand the asset refresh process\",
    \"session_id\": \"$SESSION_ID\"
  }")

echo "Request response: $REQUEST_RESPONSE"

# Step 3: Check session state
echo "3. Checking session state..."
curl -s http://localhost:8080/api/v1/sessions/$SESSION_ID | jq .

echo "=== Test Complete ==="
```

### Load Testing

Simple load test using multiple concurrent requests:

```bash
#!/bin/bash
# Simple load test

echo "=== Load Test (10 concurrent requests) ==="

for i in {1..10}; do
  (
    curl -s -X POST http://localhost:8080/api/v1/requests/web \
      -H "Content-Type: application/json" \
      -d "{
        \"user_id\": \"load-test-user-$i\",
        \"content\": \"Load test request #$i\"
      }" > /tmp/load_test_$i.json
    echo "Request $i completed"
  ) &
done

wait
echo "All requests completed. Check /tmp/load_test_*.json for results"
```

### Integration Testing

Test integration between services:

```bash
# Test Request Manager -> Agent Service flow
echo "=== Integration Test ==="

# Monitor agent service logs in another terminal:
# kubectl logs -f deployment/self-service-agent-agent-service -n $NAMESPACE

# Submit request that should trigger agent processing
curl -X POST http://localhost:8080/api/v1/requests/web \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "integration-test-user",
    "content": "Can you help me find information about employee benefits?",
    "requires_agent": true
  }' | jq .
```

## Troubleshooting

### Common Issues and Solutions

#### Port-Forward Connection Refused
```bash
# Check if service exists and has endpoints
kubectl get svc self-service-agent-request-manager-00001-private -n $NAMESPACE
kubectl get endpoints self-service-agent-request-manager-00001-private -n $NAMESPACE

# Check pod status
kubectl get pods -n $NAMESPACE | grep request-manager
```

#### Service Not Responding
```bash
# Check Knative service status
kubectl get ksvc -n $NAMESPACE

# Check pod logs
kubectl logs -l app=self-service-agent-request-manager -n $NAMESPACE --tail=50
```

#### Authentication Errors
```bash
# For testing, you may need to configure secrets
kubectl get secrets -n $NAMESPACE | grep -E "(api-keys|slack|integration)"

# Check if secrets are properly mounted
kubectl describe pod -l app=self-service-agent-request-manager -n $NAMESPACE
```

### Debugging Commands

```bash
# Get detailed service information
kubectl describe ksvc self-service-agent-request-manager -n $NAMESPACE

# Check service mesh configuration (if enabled)
kubectl get authorizationpolicy,requestauthentication -n $NAMESPACE

# Monitor real-time logs
kubectl logs -f -l app=self-service-agent-request-manager -n $NAMESPACE

# Check resource usage
kubectl top pods -n $NAMESPACE
```

## Testing Automation

### Automated Test Suite

Create a comprehensive test script:

```bash
#!/bin/bash
# automated-port-forward-tests.sh

set -e

NAMESPACE=${1:-tommy}
BASE_URL="http://localhost"

echo "=== Automated Port-Forward Test Suite ==="
echo "Namespace: $NAMESPACE"

# Check if port-forwards are active
check_service() {
  local service=$1
  local port=$2
  echo -n "Checking $service on port $port... "
  if curl -s -f "$BASE_URL:$port/health" > /dev/null; then
    echo "✓ OK"
    return 0
  else
    echo "✗ FAILED"
    return 1
  fi
}

# Health checks
echo "--- Health Check Tests ---"
check_service "Request Manager" 8080
check_service "Agent Service" 8081
check_service "Asset Manager" 8082
check_service "Integration Dispatcher" 8083

# API tests
echo "--- API Tests ---"
echo -n "Testing web request API... "
RESPONSE=$(curl -s -X POST $BASE_URL:8080/api/v1/requests/web \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "content": "test"}')

if echo "$RESPONSE" | grep -q "user_id\|request_id\|error"; then
  echo "✓ OK"
else
  echo "✗ FAILED"
fi

echo "=== Test Suite Complete ==="
```

Make it executable and run:
```bash
chmod +x automated-port-forward-tests.sh
./automated-port-forward-tests.sh your-namespace
```

## Best Practices

1. **Use Separate Terminals**: Keep each port-forward in its own terminal for easier management
2. **Use Consistent Ports**: Stick to the port mapping (8080-8083) for consistency
3. **Monitor Logs**: Keep service logs open in additional terminals for debugging
4. **Test Incrementally**: Start with health checks, then move to API testing
5. **Use JSON Tools**: Install `jq` for better JSON response formatting
6. **Script Common Tasks**: Automate repetitive testing scenarios
7. **Document Test Cases**: Keep track of what works for future reference

## Production Considerations

While port-forwarding is excellent for testing, remember:

- **Security**: Port-forwards bypass security policies - use only for development/testing
- **Performance**: Not suitable for production traffic or load testing
- **Availability**: Port-forwards are tied to your local machine connection
- **Scalability**: Limited to single pod access, doesn't test load balancing

For production access, refer to the main deployment documentation for proper ingress configuration.

## Next Steps

- Review the main [README.md](README.md) for overall project information
- Check [REQUEST_MANAGEMENT.md](REQUEST_MANAGEMENT.md) for detailed API documentation
- See [DEPLOYMENT_SERVICE_MESH.md](DEPLOYMENT_SERVICE_MESH.md) for production deployment
- Explore [examples/](examples/) directory for more testing scenarios
