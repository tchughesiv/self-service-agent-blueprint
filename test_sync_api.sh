#!/bin/bash

# Test script for synchronous API endpoint
# This demonstrates how curl can wait for AI responses

set -e

NAMESPACE=${NAMESPACE:-tommy}

# Get the OpenShift Route URL
ROUTE_HOST=$(kubectl get route self-service-agent-request-manager -n "$NAMESPACE" -o jsonpath='{.spec.host}' 2>/dev/null || echo "")
if [ -n "$ROUTE_HOST" ]; then
    REQUEST_MANAGER_URL="https://$ROUTE_HOST"
    echo -e "${BLUE}🌐 Using OpenShift Route: $REQUEST_MANAGER_URL${NC}"
else
    REQUEST_MANAGER_URL="http://localhost:8081"
    echo -e "${YELLOW}⚠️  Route not found, falling back to port-forward: $REQUEST_MANAGER_URL${NC}"
fi

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${BLUE}🧪 Testing Synchronous API Endpoint${NC}"
echo "=================================================="

# Check if Request Manager is accessible
echo -e "${BLUE}🔍 Testing connection to Request Manager...${NC}"
if curl -s --max-time 10 --insecure "$REQUEST_MANAGER_URL/health" > /dev/null 2>&1; then
    echo -e "${GREEN}✅ Request Manager is accessible${NC}"
elif [ "$REQUEST_MANAGER_URL" != "http://localhost:8081" ]; then
    echo -e "${YELLOW}⚠️  Route not accessible, trying port-forward fallback...${NC}"
    REQUEST_MANAGER_URL="http://localhost:8081"
    kubectl port-forward -n "$NAMESPACE" svc/self-service-agent-request-manager 8081:80 &
    PORT_FORWARD_PID=$!
    sleep 5
    
    # Verify port-forward connection
    if ! curl -s --max-time 5 "$REQUEST_MANAGER_URL/health" > /dev/null 2>&1; then
        echo -e "${RED}❌ Failed to connect via port-forward${NC}"
        kill $PORT_FORWARD_PID 2>/dev/null || true
        exit 1
    fi
    echo -e "${GREEN}✅ Port-forward established${NC}"
else
    echo -e "${RED}❌ Request Manager not accessible${NC}"
    exit 1
fi

echo ""
echo -e "${BLUE}📤 Testing Asynchronous API (current behavior)${NC}"
echo "curl -X POST $REQUEST_MANAGER_URL/api/v1/requests/generic"

ASYNC_RESPONSE=$(curl -s --insecure -X POST "$REQUEST_MANAGER_URL/api/v1/requests/generic" \
    -H "Content-Type: application/json" \
    -d '{
        "user_id": "sync-test-user",
        "integration_type": "CLI", 
        "content": "Please introduce yourself and tell me how you can help",
        "request_type": "question",
        "target_agent_id": "routing-agent",
        "metadata": {"test": "async"}
    }' 2>/dev/null || echo '{"error": "request failed"}')

echo "Response (immediate):"
echo "$ASYNC_RESPONSE" | jq '.' 2>/dev/null || echo "$ASYNC_RESPONSE"

echo ""
echo -e "${YELLOW}⏱️  Async response returns immediately - no AI content${NC}"

echo ""
echo "=================================================="
echo -e "${BLUE}📤 Testing Synchronous API (NEW - waits for AI)${NC}"
echo "curl -X POST $REQUEST_MANAGER_URL/api/v1/requests/generic/sync"

echo -e "${YELLOW}⏳ This will wait up to 30 seconds for AI response...${NC}"

START_TIME=$(date +%s)

SYNC_RESPONSE=$(curl -s --insecure -X POST "$REQUEST_MANAGER_URL/api/v1/requests/generic/sync?timeout=30" \
    -H "Content-Type: application/json" \
    -d '{
        "user_id": "sync-test-user", 
        "integration_type": "CLI",
        "content": "Please introduce yourself and tell me how you can help. Be concise.",
        "request_type": "question", 
        "target_agent_id": "routing-agent",
        "metadata": {"test": "sync"}
    }' 2>/dev/null || echo '{"error": "request failed"}')

END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo "Response (after ${ELAPSED}s):"
echo "$SYNC_RESPONSE" | jq '.' 2>/dev/null || echo "$SYNC_RESPONSE"

# Check if we got an AI response
if echo "$SYNC_RESPONSE" | jq -e '.response.content' > /dev/null 2>&1; then
    echo ""
    echo -e "${GREEN}✅ SUCCESS: Received AI response!${NC}"
    echo -e "${BLUE}AI Response:${NC}"
    echo "$SYNC_RESPONSE" | jq -r '.response.content' 2>/dev/null
    echo ""
    echo -e "${GREEN}📊 Processing took ${ELAPSED} seconds${NC}"
elif echo "$SYNC_RESPONSE" | grep -q "timed out"; then
    echo ""
    echo -e "${YELLOW}⏰ TIMEOUT: Request took longer than 120 seconds${NC}"
elif echo "$SYNC_RESPONSE" | grep -q "error"; then
    echo ""
    echo -e "${RED}❌ ERROR: Request failed${NC}"
    echo "$SYNC_RESPONSE"
else
    echo ""
    echo -e "${YELLOW}⚠️  Unexpected response format${NC}"
fi

# Extract session_id from the sync response for follow-up
SESSION_ID=$(echo "$SYNC_RESPONSE" | jq -r '.session_id // empty' 2>/dev/null)

if [ -n "$SESSION_ID" ]; then
    echo ""
    echo "=================================================="
    echo -e "${BLUE}📤 Testing Follow-up Laptop Refresh (Same Session)${NC}"
    echo "curl -X POST $REQUEST_MANAGER_URL/api/v1/requests/generic/sync"
    echo -e "${YELLOW}⏳ Continuing conversation with session_id: ${SESSION_ID}${NC}"

    START_TIME2=$(date +%s)

    LAPTOP_RESPONSE=$(curl -s --insecure -X POST "$REQUEST_MANAGER_URL/api/v1/requests/generic/sync?timeout=30" \
        -H "Content-Type: application/json" \
        -d "{
            \"user_id\": \"sync-test-user\", 
            \"integration_type\": \"CLI\",
            \"content\": \"refresh my laptop\",
            \"request_type\": \"request\", 
            \"target_agent_id\": \"routing-agent\",
            \"session_id\": \"$SESSION_ID\",
            \"metadata\": {\"test\": \"laptop-refresh-followup\", \"department\": \"engineering\"}
        }" 2>/dev/null || echo '{"error": "request failed"}')

    END_TIME2=$(date +%s)
    ELAPSED2=$((END_TIME2 - START_TIME2))

echo ""
echo "Response (after ${ELAPSED2}s):"
echo "$LAPTOP_RESPONSE" | jq '.' 2>/dev/null || echo "$LAPTOP_RESPONSE"

# Check if we got an AI response
if echo "$LAPTOP_RESPONSE" | jq -e '.response.content' > /dev/null 2>&1; then
    echo ""
    echo -e "${GREEN}✅ SUCCESS: Received laptop refresh response!${NC}"
    echo -e "${BLUE}AI Response:${NC}"
    echo "$LAPTOP_RESPONSE" | jq -r '.response.content' 2>/dev/null
    echo ""
    echo -e "${GREEN}📊 Processing took ${ELAPSED2} seconds${NC}"
elif echo "$LAPTOP_RESPONSE" | grep -q "timed out"; then
    echo ""
    echo -e "${YELLOW}⏰ TIMEOUT: Request took longer than 30 seconds${NC}"
elif echo "$LAPTOP_RESPONSE" | grep -q "error"; then
    echo ""
    echo -e "${RED}❌ ERROR: Request failed${NC}"
    echo "$LAPTOP_RESPONSE"
else
    echo ""
    echo -e "${YELLOW}⚠️  Unexpected response format${NC}"
fi

else
    echo ""
    echo -e "${YELLOW}⚠️  Could not extract session_id for follow-up test${NC}"
fi

echo ""
echo "=================================================="
echo -e "${BLUE}🔍 Comparison Summary${NC}"
echo ""
echo -e "${YELLOW}Asynchronous API (/api/v1/requests/generic):${NC}"
echo "  ✅ Returns immediately (~1s)"
echo "  ❌ No AI content - just request_id"  
echo "  📱 AI response comes via notifications (Slack, email, etc.)"
echo "  🎯 Best for: Web apps, mobile apps, chat bots"
echo "  🤖 Uses: routing-agent for intelligent request routing"
echo ""
echo -e "${YELLOW}Synchronous API (/api/v1/requests/generic/sync):${NC}"
echo "  ⏳ Waits for AI response (30-120s)"
echo "  ✅ Returns complete AI content"
echo "  🔧 Perfect for: curl, CLI tools, scripts"
echo "  ⚙️  Configurable timeout (default: 120s)"
echo "  🤖 Uses: routing-agent for intelligent request routing"

# Cleanup
if [ ! -z "$PORT_FORWARD_PID" ]; then
    echo ""
    echo -e "${BLUE}🧹 Cleaning up port-forward...${NC}"
    kill $PORT_FORWARD_PID 2>/dev/null || true
fi

echo ""
echo -e "${BLUE}🤖 About the Routing Agent:${NC}"
echo "  • The routing-agent is the default agent that handles initial requests"
echo "  • It can introduce itself and explain available capabilities"
echo "  • It may route requests to specialized agents (laptop-refresh, email-change, etc.)"
echo "  • Maintains conversation context using session_id"
echo "  • Based on llama-stack agent framework with integrated tools"
echo ""
echo -e "${GREEN}🎉 Synchronous API test complete!${NC}"
echo -e "${BLUE}📋 Tests performed:${NC}"
echo "  1. ⚡ Asynchronous API - General introduction request"
echo "  2. 🔄 Synchronous API - General introduction request"  
echo "  3. 💬 Synchronous API - Follow-up laptop refresh (same session)"
