# Migration from Knative Services to Deployments + Services

## Summary
Based on testing and operational challenges, we recommend migrating from Knative Services to standard Kubernetes Deployments + Services.

## What We Keep vs What We Lose

### ✅ **What We Keep:**
- **CloudEvent Infrastructure**: Knative Eventing (broker, triggers) remains unchanged
- **Auto-scaling**: Use HPA (Horizontal Pod Autoscaler) instead of KPA
- **Service Mesh**: Istio integration works better with regular services
- **All Application Logic**: No code changes required

### ❌ **What We Lose:**
- **Scale-to-Zero**: Not critical for our always-on services
- **Advanced Traffic Splitting**: Can implement with Istio if needed
- **Revision Management**: Use standard deployment strategies instead
- **Concurrency-based Scaling**: Use CPU/memory-based scaling instead

## Migration Benefits

### 🚀 **Immediate Fixes:**
- ✅ **External Access**: Regular services work with OpenShift routes
- ✅ **Port Forwarding**: Standard kubectl port-forward works reliably
- ✅ **Service Discovery**: Standard Kubernetes DNS resolution
- ✅ **Debugging**: Standard kubectl commands work predictably

### 📊 **Operational Improvements:**
- **Predictable Scaling**: HPA with CPU/memory metrics
- **Better Monitoring**: Standard Kubernetes metrics
- **Simpler Troubleshooting**: Standard Kubernetes patterns
- **Reliable External Access**: Works with any ingress controller

## Implementation Plan

### Phase 1: Create Deployment Templates
```yaml
# Example: agent-service-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "self-service-agent.fullname" . }}-agent-service
spec:
  replicas: {{ .Values.requestManagement.agentService.replicas | default 2 }}
  selector:
    matchLabels:
      app: {{ include "self-service-agent.fullname" . }}-agent-service
  template:
    metadata:
      labels:
        app: {{ include "self-service-agent.fullname" . }}-agent-service
        version: v1
      annotations:
        sidecar.istio.io/inject: "true"
    spec:
      containers:
      - name: agent-service
        # ... same container spec as current ksvc
        
---
apiVersion: v1
kind: Service
metadata:
  name: {{ include "self-service-agent.fullname" . }}-agent-service
spec:
  selector:
    app: {{ include "self-service-agent.fullname" . }}-agent-service
  ports:
  - port: 80
    targetPort: 8080
    
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ include "self-service-agent.fullname" . }}-agent-service
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {{ include "self-service-agent.fullname" . }}-agent-service
  minReplicas: {{ .Values.requestManagement.agentService.autoscaling.minScale }}
  maxReplicas: {{ .Values.requestManagement.agentService.autoscaling.maxScale }}
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 70
  - type: Resource
    resource:
      name: memory
      target:
        type: Utilization
        averageUtilization: 80
```

### Phase 2: Update Knative Triggers
```yaml
# Triggers remain the same, just point to regular services
spec:
  subscriber:
    uri: http://{{ include "self-service-agent.fullname" . }}-agent-service.{{ .Release.Namespace }}.svc.cluster.local
```

### Phase 3: External Access
```yaml
# OpenShift Route
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: {{ include "self-service-agent.fullname" . }}-request-manager
spec:
  to:
    kind: Service
    name: {{ include "self-service-agent.fullname" . }}-request-manager
  port:
    targetPort: 8080
  tls:
    termination: edge
```

## Migration Steps

1. **Create new Deployment templates** alongside existing ksvc
2. **Test with feature flag** to switch between ksvc and deployment
3. **Update DNS/service discovery** to point to new services
4. **Migrate one service at a time** starting with request-manager
5. **Verify CloudEvent flow** works with new services
6. **Remove Knative Services** once all are migrated

## Values.yaml Changes

```yaml
requestManagement:
  # Add deployment toggle
  useKnativeServices: false  # Switch to false for migration
  
  agentService:
    replicas: 2  # Replace minScale with fixed replicas initially
    autoscaling:
      enabled: true
      minReplicas: 1
      maxReplicas: 20
      targetCPUUtilization: 70
      targetMemoryUtilization: 80
```

## Testing Strategy

1. **Deploy alongside existing**: Run both ksvc and deployment
2. **Internal testing**: Verify service-to-service communication
3. **CloudEvent testing**: Confirm broker → deployment flow works
4. **External access**: Test routes/ingress work properly
5. **Load testing**: Verify HPA scaling works correctly
6. **Rollback plan**: Keep ksvc templates for quick rollback

## Expected Outcomes

### ✅ **Immediate Improvements:**
- External access works reliably
- Port forwarding for debugging
- Standard Kubernetes troubleshooting
- Predictable service behavior

### 📊 **Long-term Benefits:**
- Easier onboarding for team members
- Better integration with monitoring tools
- More predictable resource usage
- Simpler CI/CD pipelines

## Risk Mitigation

- **Gradual Migration**: One service at a time
- **Feature Flags**: Easy rollback mechanism
- **Parallel Deployment**: Run both during transition
- **Comprehensive Testing**: Verify all flows work
- **Documentation**: Update operational procedures

## Conclusion

The migration to Deployments + Services will solve our current operational challenges while maintaining all core functionality. The trade-offs (losing scale-to-zero, advanced traffic management) are acceptable given our use case requirements and the operational benefits gained.
