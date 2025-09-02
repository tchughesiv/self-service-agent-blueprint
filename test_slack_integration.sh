#!/bin/bash

# Test Slack Integration Setup
# This script tests the Integration Dispatcher route and Slack endpoints

set -e

NAMESPACE=${NAMESPACE:-tommy}

echo "==========================================="
echo "🔗 Testing Slack Integration Setup"
echo "==========================================="

# Get Integration Dispatcher route
echo "📡 Getting Integration Dispatcher route..."
INTEGRATION_DISPATCHER_ROUTE=$(kubectl get route -n $NAMESPACE -o jsonpath='{.items[?(@.metadata.name=="self-service-agent-integration-dispatcher")].spec.host}' 2>/dev/null || echo "")

if [ -z "$INTEGRATION_DISPATCHER_ROUTE" ]; then
    echo "❌ Integration Dispatcher route not found!"
    echo "Make sure you've deployed the Helm chart with the updated external-routes.yaml"
    exit 1
fi

INTEGRATION_DISPATCHER_URL="https://$INTEGRATION_DISPATCHER_ROUTE"
echo "✅ Found Integration Dispatcher route: $INTEGRATION_DISPATCHER_URL"

# Test health endpoint
echo ""
echo "🏥 Testing Integration Dispatcher health..."
if curl -s --insecure "$INTEGRATION_DISPATCHER_URL/health" | grep -q "healthy"; then
    echo "✅ Integration Dispatcher is healthy"
else
    echo "❌ Integration Dispatcher health check failed"
    exit 1
fi

# Test Slack endpoints exist (should return 405 Method Not Allowed for GET)
echo ""
echo "🔍 Testing Slack endpoints..."

endpoints=("slack/events" "slack/interactive" "slack/commands")
for endpoint in "${endpoints[@]}"; do
    echo "Testing /$endpoint..."
    status_code=$(curl -s --insecure -o /dev/null -w "%{http_code}" "$INTEGRATION_DISPATCHER_URL/$endpoint" || echo "000")
    
    if [ "$status_code" = "405" ]; then
        echo "✅ /$endpoint endpoint exists (returns 405 for GET as expected)"
    elif [ "$status_code" = "422" ]; then
        echo "✅ /$endpoint endpoint exists (returns 422 for missing data as expected)"
    else
        echo "❌ /$endpoint endpoint issue (status: $status_code)"
    fi
done

echo ""
echo "==========================================="
echo "📋 Slack App Configuration URLs:"
echo "==========================================="
echo "Event Subscriptions URL:"
echo "  $INTEGRATION_DISPATCHER_URL/slack/events"
echo ""
echo "Interactivity URL:"
echo "  $INTEGRATION_DISPATCHER_URL/slack/interactive"
echo ""
echo "Slash Commands URL:"
echo "  $INTEGRATION_DISPATCHER_URL/slack/commands"
echo ""
echo "==========================================="
echo "🎉 Integration Dispatcher is ready for Slack!"
echo "Use the URLs above in your Slack app configuration."
echo "==========================================="
