# Synchronous API Implementation & Fixes

## Overview
Successfully implemented and debugged a complete synchronous API that allows curl and CLI tools to wait for AI responses, fixing multiple issues in the process.

## 🎯 **Final Result: Working Synchronous API!**

### **Asynchronous API** (Original)
```bash
curl -X POST .../api/v1/requests/generic
# Returns immediately: {"request_id": "abc123", "status": "accepted"}
```

### **Synchronous API** (NEW - Working!)
```bash
curl -X POST .../api/v1/requests/generic/sync?timeout=120
# Waits 30-60s, then returns: {"status": "completed", "response": {"content": "AI response here..."}}
```

## 🐛 **Issues Fixed**

### **1. Stream Response Bug** ❌➡️✅
**Problem**: Agent Service was storing `<llama_stack_client.Stream object>` instead of actual AI response content.

**Root Cause**: Fallback logic in stream processing was converting exhausted stream object to string.

**Fix**: 
```python
# OLD (BROKEN)
if not content:
    content = str(response_stream)  # Stores "<Stream object at 0x...>"

# NEW (FIXED)  
if not content:
    content = f"No response content received from agent (processed {chunk_count} chunks)"
    logger.warning("No content collected from stream", chunk_count=chunk_count)
```

**Files Changed**: `agent-service/src/agent_service/main.py`

### **2. Missing Agent Response Routing** ❌➡️✅
**Problem**: Agent Service responses only went to Integration Dispatcher, not back to Request Manager for database storage.

**Root Cause**: Missing Knative Trigger to route `com.self-service-agent.agent.response-ready` events to Request Manager.

**Fix**: Added new trigger in `helm/templates/knative-triggers.yaml`:
```yaml
apiVersion: eventing.knative.dev/v1
kind: Trigger
metadata:
  name: agent-response-to-request-manager-trigger
spec:
  broker: self-service-agent-broker
  filter:
    attributes:
      type: com.self-service-agent.agent.response-ready
  subscriber:
    uri: http://request-manager.svc.cluster.local/api/v1/events/cloudevents
```

### **3. Agent Mapping Staleness** ❌➡️✅
**Problem**: Agent Service cached stale agent IDs when agents were recreated in llama-stack.

**Root Cause**: Agent Service built mapping once at startup and never refreshed it.

**Fix**: 
- ✅ **On-demand refresh**: When agent lookup fails, automatically refresh mapping
- ✅ **Periodic refresh**: Background task refreshes mapping every 5 minutes
- ✅ **Better error handling**: Proper exceptions instead of fallback to agent name

**Configuration**:
```bash
AGENT_REFRESH_INTERVAL=300  # 5 minutes (set to 0 to disable)
```

### **4. OpenShift Route Timeout** ❌➡️✅
**Problem**: OpenShift Route had 30-second timeout, shorter than AI processing time.

**Fix**: Added HAProxy timeout annotation:
```yaml
annotations:
  haproxy.router.openshift.io/timeout: "180s"
```

### **5. Agent ID Resolution Fallback Bug** ❌➡️✅
**Problem**: When agent lookup failed, service returned agent name instead of proper error.

**Root Cause**: Code used `return agent_name` as fallback, but llama-stack expects valid agent IDs.

**Fix**: Changed to raise proper exception with helpful error message:
```python
raise ValueError(
    f"Agent '{agent_name}' not found in llama-stack. "
    f"Available agents: {available_agents}. "
    f"Please check agent configuration or create the agent in llama-stack."
)
```

## 🔧 **Architecture Changes**

### **Event Flow (Complete)**
```
1. curl → Request Manager /sync endpoint ✅
2. Request Manager publishes to Kafka ✅  
3. Agent Service receives request ✅
4. Agent Service processes with llama-stack ✅
5. Agent Service publishes response ✅
6. Request Manager receives response (NEW) ✅
7. Request Manager stores in database ✅
8. Sync API polls database and finds response ✅
9. Response contains actual AI content (FIXED) ✅
10. curl receives complete AI response ✅
```

### **Database Schema**
The sync API uses existing `request_logs` table:
- `request_id`: Links request to response
- `response_content`: Now contains actual AI response (not Stream object)
- `agent_id`: Properly resolved agent ID
- `completed_at`: Timestamp when response was stored

### **New Configuration Options**
```yaml
# helm/values.yaml
requestManagement:
  externalAccess:
    enabled: true
    method: "route"  # Uses OpenShift Route with extended timeout

# Environment variables
AGENT_REFRESH_INTERVAL: "300"  # Periodic agent mapping refresh (seconds)
```

## 🧪 **Testing**

### **Test Script**
`test_sync_api.sh` demonstrates both async and sync APIs:
```bash
./test_sync_api.sh
# Automatically detects OpenShift Route
# Falls back to port-forward if needed
# Tests both async and sync endpoints
# Shows response time comparison
```

### **Manual Testing**
```bash
# Test sync API with custom timeout
curl -X POST "https://route-url/api/v1/requests/generic/sync?timeout=60" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "test", "integration_type": "CLI", "content": "What is 2+2?", "request_type": "question"}'

# Expected response:
{
  "request_id": "abc123",
  "session_id": "def456",
  "status": "completed", 
  "response": {
    "agent_id": "d8e79fd0-1f19-45e6-bf06-0e89d5eafd9e",
    "content": "2 + 2 = 4",
    "processing_time_ms": 1240
  }
}
```

## 🎁 **Benefits**

### **For Users**
- ✅ **curl-friendly**: Single command gets complete AI response
- ✅ **Script-friendly**: No complex notification handling needed  
- ✅ **Configurable timeout**: Adjust based on use case (default 120s)
- ✅ **Error handling**: Clear error messages for timeouts/failures

### **For Operations**  
- ✅ **Same backend**: Uses existing event-driven architecture
- ✅ **Auto-recovery**: Agent mapping refreshes automatically
- ✅ **Monitoring**: Full logging and observability
- ✅ **Backwards compatible**: Async API unchanged

### **For Development**
- ✅ **Testing**: Easy to test AI responses in scripts
- ✅ **Debugging**: Immediate feedback for development
- ✅ **Integration**: Perfect for CI/CD and automation

## 📊 **Performance**

### **Response Times**
- **Async API**: ~1 second (immediate acknowledgment)
- **Sync API**: ~30-60 seconds (includes AI processing)
- **Database polling**: 1-second intervals (efficient)

### **Resource Usage**
- **Memory**: Minimal impact (reuses existing infrastructure)
- **CPU**: Slight increase from periodic agent refresh (every 5 minutes)
- **Network**: Same event flow, just additional database storage

## 🚀 **Deployment**

### **Required Updates**
1. **Agent Service**: Stream processing fixes + agent refresh logic
2. **Helm Chart**: New Knative trigger + route timeout
3. **Request Manager**: Already had sync endpoint (deployed previously)

### **Configuration**
```bash
# Deploy with agent refresh enabled
AGENT_REFRESH_INTERVAL=300 make helm-install

# Or disable periodic refresh (only on-demand)
AGENT_REFRESH_INTERVAL=0 make helm-install
```

## 🎉 **Success Metrics**

- ✅ **Sync API works end-to-end**
- ✅ **Actual AI responses (not Stream objects)**
- ✅ **Agent mapping auto-refreshes**
- ✅ **Proper error handling**
- ✅ **Route timeout extended**
- ✅ **All tests pass**
- ✅ **Linting clean**

The synchronous API is now **production-ready** and provides a complete alternative to the async notification-based flow! 🎊
