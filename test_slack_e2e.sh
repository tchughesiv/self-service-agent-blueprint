#!/bin/bash

# Test Slack Integration with E2E User
# This script tests the full Slack integration flow using the e2e-test-user-fixed

set -e

NAMESPACE=${NAMESPACE:-tommy}
TEST_USER="e2e-test-user-fixed"

echo "==========================================="
echo "🔗 Testing Slack Integration E2E Flow"
echo "==========================================="
echo "Test User: $TEST_USER"
echo "Namespace: $NAMESPACE"

# Get routes
REQUEST_MANAGER_ROUTE=$(kubectl get route -n $NAMESPACE -o jsonpath='{.items[?(@.metadata.name=="self-service-agent-request-manager")].spec.host}' 2>/dev/null || echo "")
INTEGRATION_DISPATCHER_ROUTE=$(kubectl get route -n $NAMESPACE -o jsonpath='{.items[?(@.metadata.name=="self-service-agent-integration-dispatcher")].spec.host}' 2>/dev/null || echo "")

if [ -z "$REQUEST_MANAGER_ROUTE" ]; then
    echo "❌ Request Manager route not found!"
    exit 1
fi

if [ -z "$INTEGRATION_DISPATCHER_ROUTE" ]; then
    echo "❌ Integration Dispatcher route not found!"
    exit 1
fi

REQUEST_MANAGER_URL="https://$REQUEST_MANAGER_ROUTE"
INTEGRATION_DISPATCHER_URL="https://$INTEGRATION_DISPATCHER_ROUTE"

echo "Request Manager: $REQUEST_MANAGER_URL"
echo "Integration Dispatcher: $INTEGRATION_DISPATCHER_URL"
echo ""

# Test 1: Send request with Slack integration
echo "📤 Step 1: Sending request with Slack integration..."

TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
REQUEST_PAYLOAD=$(cat <<EOF
{
  "integration_type": "slack",
  "user_id": "$TEST_USER",
  "content": "E2E Slack test: I need help with my laptop refresh. This is a test of the Slack integration.",
  "request_type": "slack_e2e_test",
  "metadata": {
    "test_type": "slack_e2e",
    "timestamp": "$TIMESTAMP",
    "test_id": "slack-e2e-test"
  }
}
EOF
)

echo "Request payload:"
echo "$REQUEST_PAYLOAD" | jq .

RESPONSE=$(curl -s --insecure -X POST "$REQUEST_MANAGER_URL/api/v1/requests/generic" \
  -H "Content-Type: application/json" \
  -d "$REQUEST_PAYLOAD")

if [ $? -eq 0 ]; then
    echo "✅ Request sent successfully"
    echo "Response:"
    echo "$RESPONSE" | jq .
    
    # Extract session and request IDs
    SESSION_ID=$(echo "$RESPONSE" | jq -r '.session_id // empty')
    REQUEST_ID=$(echo "$RESPONSE" | jq -r '.request_id // empty')
    
    if [ -n "$SESSION_ID" ] && [ -n "$REQUEST_ID" ]; then
        echo "✅ Extracted Session ID: $SESSION_ID"
        echo "✅ Extracted Request ID: $REQUEST_ID"
    else
        echo "❌ Could not extract session/request IDs"
        exit 1
    fi
else
    echo "❌ Failed to send request"
    exit 1
fi

echo ""
echo "⏳ Step 2: Waiting for processing..."
sleep 5

# Check logs for processing
echo "📋 Step 3: Checking service logs..."

echo ""
echo "--- Request Manager Logs ---"
kubectl logs -n $NAMESPACE deployment/self-service-agent-request-manager --since=2m | tail -10

echo ""
echo "--- Agent Service Logs ---"
kubectl logs -n $NAMESPACE deployment/self-service-agent-agent-service --since=2m | tail -10

echo ""
echo "--- Integration Dispatcher Logs ---"
kubectl logs -n $NAMESPACE deployment/self-service-agent-integration-dispatcher --since=2m | tail -15

echo ""
echo "🔍 Step 4: Checking for Slack integration processing..."

# Look for Slack-specific processing in Integration Dispatcher logs
INTEGRATION_LOGS=$(kubectl logs -n $NAMESPACE deployment/self-service-agent-integration-dispatcher --since=3m 2>/dev/null)

if echo "$INTEGRATION_LOGS" | grep -q "$REQUEST_ID"; then
    echo "✅ Request ID found in Integration Dispatcher logs"
    
    if echo "$INTEGRATION_LOGS" | grep -q "SLACK"; then
        echo "✅ SLACK integration type detected"
        
        if echo "$INTEGRATION_LOGS" | grep -q "e2e-test@company.com"; then
            echo "✅ Slack user email found in processing"
        else
            echo "⚠️  Slack user email not found in logs"
        fi
        
        if echo "$INTEGRATION_LOGS" | grep -q "delivered"; then
            echo "✅ Message delivery attempted"
        else
            echo "⚠️  No delivery confirmation found"
        fi
    else
        echo "❌ SLACK integration not detected in logs"
    fi
else
    echo "❌ Request ID not found in Integration Dispatcher logs"
fi

echo ""
echo "==========================================="
echo "📋 Slack Integration Test Summary"
echo "==========================================="
echo "Request ID: $REQUEST_ID"
echo "Session ID: $SESSION_ID"
echo "Test User: $TEST_USER"
echo "Integration Type: SLACK"
echo "User Email: e2e-test@company.com"
echo ""
echo "🔧 To test with real Slack:"
echo "1. Configure Slack bot token in Integration Dispatcher"
echo "2. Set up Slack app with webhook URLs:"
echo "   - Events: $INTEGRATION_DISPATCHER_URL/slack/events"
echo "   - Interactive: $INTEGRATION_DISPATCHER_URL/slack/interactive"
echo "   - Commands: $INTEGRATION_DISPATCHER_URL/slack/commands"
echo "3. Update user_integration_configs with real Slack user email"
echo ""
echo "✅ Slack E2E test completed!"
echo "==========================================="
