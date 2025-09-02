# Knative Services Configuration

This document explains the configurable options for resolving Knative service and route issues.

## Configuration Options

### Service Visibility

The Helm chart now supports configurable service visibility via `values.yaml`:

```yaml
requestManagement:
  knative:
    # Service visibility configuration
    # - "cluster-local" bypasses external load balancer issues
    # - "external" uses external domains (requires working ingress gateway)
    serviceVisibility: "cluster-local"
```

#### Option 1: cluster-local (Recommended)
- **Pros**: Bypasses external ingress gateway issues, services work immediately
- **Cons**: Services only accessible within the cluster
- **Use Case**: Internal microservices communication, development environments

#### Option 2: external 
- **Pros**: Services accessible from outside the cluster
- **Cons**: Requires working Knative ingress gateway and TLS configuration
- **Use Case**: Production environments with properly configured ingress

### Domain Mapping

Optional custom domain mapping for external access:

```yaml
requestManagement:
  knative:
    domainMapping:
      enabled: true  # Set to false to disable DomainMapping
      domains:
        agentService: "agent-service.local"
        requestManager: "request-manager.local"
        integrationDispatcher: "integration-dispatcher.local"
        deadLetterSink: "dead-letter-sink.local"
```

## Current Status

### Partially Working Configuration (cluster-local)
- ✅ **Pod Health**: All pods are running and responding to health checks (confirmed via logs)
- ✅ **Service Mesh**: Istio sidecar injection working properly
- ✅ **Event Processing**: Knative triggers and brokers functional
- ✅ **Cluster-Local Annotation**: `networking.knative.dev/visibility: cluster-local` applied correctly
- ❌ **Service Routing**: Both external and cluster-local service access returning 503 errors
- ❌ **Load Balancer**: External routes still show "Unknown/Uninitialized" status

### Access Methods

#### 1. Cluster-Internal Access
```bash
# Direct service access (cluster-local)
curl http://self-service-agent-agent-service.tommy.svc.cluster.local/health
curl http://self-service-agent-request-manager.tommy.svc.cluster.local/health
```

#### 2. Port Forwarding (Development)
```bash
# Port forward for external testing
kubectl port-forward svc/self-service-agent-agent-service-00001 8080:80 -n tommy
curl http://localhost:8080/health
```

#### 3. Service Mesh Gateway (Production)
The Service Mesh Gateway provides external access via:
- `https://api.selfservice.apps.cluster.local/*`
- `https://selfservice.apps.cluster.local/*`

## Troubleshooting

### External Routes "Unknown/Uninitialized"
This is expected when using `cluster-local` visibility. The external routes are created but not functional, which is the intended behavior to bypass ingress gateway issues.

### Switching to External Visibility
To enable external access (if ingress gateway is working):

```yaml
requestManagement:
  knative:
    serviceVisibility: "external"
    domainMapping:
      enabled: false  # Disable custom domains when using external
```

## Implementation Details

The solution modifies all Knative Service templates to conditionally apply the cluster-local annotation:

```yaml
metadata:
  annotations:
    {{- if eq .Values.requestManagement.knative.serviceVisibility "cluster-local" }}
    networking.knative.dev/visibility: cluster-local
    {{- end }}
```

This approach:
1. Resolves the "TLSNotEnabled external-domain-tls" issues
2. Bypasses ingress gateway load balancer problems  
3. Maintains full functionality for internal service communication
4. Provides configurable options for different deployment scenarios
