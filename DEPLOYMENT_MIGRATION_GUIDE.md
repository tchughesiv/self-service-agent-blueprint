# Migration Guide: Knative Services → Deployments + Services

## Overview

This guide walks you through migrating from Knative Services to regular Kubernetes Deployments + Services while keeping the excellent Knative Eventing architecture.

## Architecture After Migration

```
External Traffic
    ↓ (OpenShift Route - TLS terminated)
Request Manager (Deployment) 
    ↓ (publishes CloudEvents)
Knative Broker (backed by Kafka)
    ↓ (filtered by Triggers)  
Agent Service (Deployment - cluster-internal)
    ↓ (publishes response CloudEvents)
Integration Dispatcher (Deployment - cluster-internal)
```

## What Changes

### ✅ **Keeps Working:**
- **Knative Eventing**: Broker, triggers, and event routing
- **Kafka Cluster**: Your reliable message storage
- **CloudEvent Flow**: Request manager → broker → agent service
- **Application Code**: No changes needed
- **Service Mesh**: Better integration with regular services

### 🔄 **What Changes:**
- **Services**: Knative Services → Deployments + Services
- **External Access**: Only request manager exposed via OpenShift Route
- **Scaling**: HPA instead of KPA (CPU/memory based vs concurrency based)
- **Debugging**: Standard kubectl commands work reliably

## Migration Steps

### Step 1: Update Configuration

Set the deployment mode in `values.yaml`:

```yaml
requestManagement:
  enabled: true
  # Switch to Deployment mode
  useKnativeServices: false  # Set to true to revert to Knative Services
```

### Step 2: Deploy New Resources

Deploy the updated Helm chart:

```bash
# Deploy new deployment-based services alongside existing
helm upgrade self-service-agent ./helm \
  --namespace tommy \
  --set requestManagement.useKnativeServices=false
```

### Step 3: Verify New Services

Check that new deployments are running:

```bash
# Check deployments
kubectl get deployments -n tommy

# Check services  
kubectl get services -n tommy

# Check external route (only request manager)
kubectl get routes -n tommy

# Check HPA scaling
kubectl get hpa -n tommy
```

### Step 4: Test the Flow

Test external access:

```bash
# Get the route URL
ROUTE_URL=$(kubectl get route self-service-agent-request-manager -n tommy -o jsonpath='{.spec.host}')

# Test health endpoint
curl -k "https://$ROUTE_URL/health"

# Test session creation
curl -k -X POST "https://$ROUTE_URL/api/v1/sessions" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "migration-test-user",
    "integration_type": "web",
    "integration_metadata": {"source": "migration-test"}
  }'
```

Test internal service connectivity:

```bash
# Port forward to request manager (should work now!)
kubectl port-forward -n tommy svc/self-service-agent-request-manager 8080:80

# Test from another terminal
curl http://localhost:8080/health
```

### Step 5: Verify Event Flow

Send a test request and monitor the event flow:

```bash
# Monitor agent service logs
kubectl logs -n tommy -l app=self-service-agent-agent-service -f &

# Send test request
curl -k -X POST "https://$ROUTE_URL/api/v1/requests/generic" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "migration-test-user",
    "integration_type": "web", 
    "request_type": "message",
    "content": "Test request to verify broker → agent flow after migration",
    "metadata": {"test": "migration"}
  }'

# Check if agent service receives the event
```

### Step 6: Clean Up Old Resources (Optional)

Once you've verified everything works, you can remove the old Knative Services:

```bash
# Remove Knative Services (be careful!)
kubectl delete ksvc -n tommy self-service-agent-request-manager
kubectl delete ksvc -n tommy self-service-agent-agent-service  
kubectl delete ksvc -n tommy self-service-agent-integration-dispatcher
```

## Configuration Details

### Deployment Scaling Configuration

```yaml
requestManagement:
  requestManager:
    replicas: 2  # Fixed replicas
    autoscaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 10
      targetCPUUtilization: 70
      targetMemoryUtilization: 80

  agentService:
    replicas: 2
    autoscaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 20
      targetCPUUtilization: 70  # More aggressive scaling for processing
      targetMemoryUtilization: 80

  integrationDispatcher:
    replicas: 2
    autoscaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 5
      targetCPUUtilization: 70
      targetMemoryUtilization: 80
```

### Security Configuration

```yaml
# Only request manager is externally accessible
requestManagement:
  certificates:
    domains:
      requestManager: "your-domain.com"
      # agentService and integrationDispatcher are cluster-internal only
```

## Troubleshooting

### Issue: Services not starting

**Check deployment status:**
```bash
kubectl get deployments -n tommy
kubectl describe deployment self-service-agent-request-manager -n tommy
```

**Check pod logs:**
```bash
kubectl logs -n tommy -l app=self-service-agent-request-manager
```

### Issue: External route not accessible

**Check route configuration:**
```bash
kubectl get route -n tommy
kubectl describe route self-service-agent-request-manager -n tommy
```

**Verify service endpoints:**
```bash
kubectl get endpoints -n tommy
```

### Issue: Event flow not working

**Check Knative triggers:**
```bash
kubectl get triggers -n tommy
kubectl describe trigger self-service-agent-request-created-trigger -n tommy
```

**Check broker status:**
```bash
kubectl get broker -n tommy
kubectl describe broker self-service-agent-broker -n tommy
```

**Monitor Kafka topics:**
```bash
kubectl exec -n tommy self-service-agent-kafka-self-service-agent-kafka-pool-0 -- \
  /opt/kafka/bin/kafka-console-consumer.sh \
  --bootstrap-server localhost:9092 \
  --topic knative-broker-tommy-self-service-agent-broker \
  --from-beginning --max-messages 5
```

## Rollback Plan

If you need to rollback to Knative Services:

```bash
# Revert configuration
helm upgrade self-service-agent ./helm \
  --namespace tommy \
  --set requestManagement.useKnativeServices=true

# Clean up deployment resources (if needed)
kubectl delete deployment -n tommy -l component=request-manager
kubectl delete deployment -n tommy -l component=agent-service
kubectl delete deployment -n tommy -l component=integration-dispatcher
kubectl delete hpa -n tommy -l app.kubernetes.io/instance=self-service-agent
kubectl delete route -n tommy self-service-agent-request-manager
```

## Benefits After Migration

### ✅ **Operational Improvements:**
- **Reliable External Access**: OpenShift routes work consistently
- **Better Debugging**: Standard kubectl port-forward, logs, exec
- **Predictable Scaling**: HPA with well-understood CPU/memory metrics
- **Easier Monitoring**: Standard Kubernetes metrics and dashboards

### ✅ **Security Improvements:**
- **Reduced Attack Surface**: Only request manager externally accessible
- **Better Network Isolation**: Agent service and dispatcher cluster-internal
- **Standard Security Policies**: Works with standard Kubernetes security

### ✅ **Development Experience:**
- **Faster Debugging**: No "Uninitialized" states or routing mysteries
- **Standard Tools**: All kubectl commands work as expected
- **Easier Onboarding**: Standard Kubernetes patterns

## Conclusion

The migration provides significant operational benefits while maintaining all the powerful event-driven architecture you've built. The trade-offs (losing scale-to-zero and advanced traffic management) are minimal compared to the gains in reliability and debuggability.

Your investment in Knative Eventing pays off - the event flow continues to work perfectly, just with more reliable service endpoints!
