# Deployment Guide with OpenShift Service Mesh

This guide covers deploying the Request Management Layer with full OpenShift Service Mesh (Istio) integration for production environments.

## Prerequisites

### OpenShift Service Mesh Operator
Ensure the OpenShift Service Mesh Operator is installed:

```bash
# Check if Service Mesh is installed
oc get csv -n openshift-operators | grep servicemesh

# If not installed, install via OperatorHub or:
cat <<EOF | oc apply -f -
apiVersion: operators.coreos.com/v1alpha1
kind: Subscription
metadata:
  name: servicemeshoperator
  namespace: openshift-operators
spec:
  channel: stable
  name: servicemeshoperator
  source: redhat-operators
  sourceNamespace: openshift-marketplace
EOF
```

### Service Mesh Control Plane
Create the Service Mesh Control Plane:

```bash
cat <<EOF | oc apply -f -
apiVersion: maistra.io/v2
kind: ServiceMeshControlPlane
metadata:
  name: basic
  namespace: istio-system
spec:
  version: v2.4
  tracing:
    type: Jaeger
    sampling: 10000
  addons:
    jaeger:
      name: jaeger
    kiali:
      enabled: true
      name: kiali
    grafana:
      enabled: true
EOF
```

### Service Mesh Member Roll
**Note:** The Helm chart now automatically creates a `ServiceMeshMember` resource to add the deployment namespace to the Service Mesh. This eliminates the need to manually update the ServiceMeshMemberRoll.

The chart creates the following resource automatically:
```yaml
apiVersion: maistra.io/v1
kind: ServiceMeshMember
metadata:
  name: default
  namespace: <your-namespace>
spec:
  controlPlaneRef:
    namespace: istio-system
    name: data-science-smcp  # Configurable via values.yaml
```

If you need to manually add the namespace to the Service Mesh Member Roll instead, use:

```bash
cat <<EOF | oc apply -f -
apiVersion: maistra.io/v1
kind: ServiceMeshMemberRoll
metadata:
  name: default
  namespace: istio-system
spec:
  members:
  - llama-stack-rag
  - knative-eventing
  - knative-serving
EOF
```

## Step 1: Create Namespace and Secrets

```bash
# Create namespace
oc create namespace llama-stack-rag
oc label namespace llama-stack-rag istio-injection=enabled

# Create API keys secret
oc create secret generic api-keys \
  --from-literal=snow-integration="your-snow-api-key" \
  --from-literal=hr-system="your-hr-api-key" \
  --from-literal=monitoring-system="your-monitoring-api-key" \
  -n llama-stack-rag

# Create Slack signing secret
oc create secret generic slack-signing-secret \
  --from-literal=signing-secret="your-slack-signing-secret" \
  -n llama-stack-rag

# Create TLS certificate for gateway (replace with your cert)
oc create secret tls selfservice-tls-secret \
  --cert=path/to/tls.crt \
  --key=path/to/tls.key \
  -n llama-stack-rag
```

## Step 2: Deploy Service Mesh Configurations

```bash
# Apply Service Mesh gateway and security policies
oc apply -f service-mesh/gateway.yaml
oc apply -f service-mesh/security.yaml
oc apply -f service-mesh/telemetry.yaml
```

## Step 3: Deploy Knative Event Infrastructure

```bash
# Deploy Knative broker and triggers
oc apply -f knative/broker.yaml
oc apply -f knative/triggers.yaml
```

## Step 4: Deploy Services

```bash
# Build and push container images
make build-all-images
make push-all-images

# Deploy Request Manager service
oc apply -f knative/request-manager-service.yaml

# Deploy Agent service
oc apply -f knative/agent-service.yaml

# Wait for services to be ready
oc wait --for=condition=Ready ksvc/request-manager -n llama-stack-rag --timeout=300s
oc wait --for=condition=Ready ksvc/agent-service -n llama-stack-rag --timeout=300s
```

## Step 5: Configure External Access

### Route Configuration
```bash
# Create OpenShift route that uses the Istio gateway
cat <<EOF | oc apply -f -
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: selfservice-api
  namespace: llama-stack-rag
  annotations:
    haproxy.router.openshift.io/timeout: 60s
spec:
  host: api.selfservice.apps.cluster.local
  to:
    kind: Service
    name: istio-ingressgateway
    weight: 100
  port:
    targetPort: http2
  tls:
    termination: passthrough
    insecureEdgeTerminationPolicy: Redirect
EOF
```

### DNS Configuration
Ensure your DNS points to the OpenShift cluster:
```bash
# Example DNS entries needed
api.selfservice.apps.cluster.local -> <cluster-ingress-ip>
selfservice.apps.cluster.local -> <cluster-ingress-ip>
```

## Step 6: Configure Authentication

### JWT Provider Setup
If using external JWT provider (e.g., Red Hat SSO):

```bash
# Update the RequestAuthentication in security.yaml with your issuer
# Example for Red Hat SSO:
cat <<EOF | oc apply -f -
apiVersion: security.istio.io/v1beta1
kind: RequestAuthentication
metadata:
  name: jwt-auth
  namespace: llama-stack-rag
spec:
  selector:
    matchLabels:
      app: request-manager
  jwtRules:
  - issuer: "https://sso.redhat.com/auth/realms/your-realm"
    jwksUri: "https://sso.redhat.com/auth/realms/your-realm/protocol/openid-connect/certs"
    audiences:
    - "selfservice-api"
EOF
```

## Step 7: Verify Deployment

### Health Checks
```bash
# Check service health
curl -k https://api.selfservice.apps.cluster.local/health

# Check Service Mesh status
oc get smcp -n istio-system
oc get smmr -n istio-system

# Verify pod injection
oc get pods -n llama-stack-rag -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[*].name}{"\n"}{end}'
```

### Test Integration Endpoints

#### Web Request (requires JWT)
```bash
# Get JWT token from your auth provider
TOKEN="your-jwt-token"

curl -X POST https://api.selfservice.apps.cluster.local/api/v1/requests/web \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test.user",
    "content": "Hello, I need help with my laptop",
    "client_ip": "192.168.1.1"
  }'
```

#### Tool Request (requires API key)
```bash
curl -X POST https://api.selfservice.apps.cluster.local/api/v1/requests/tool \
  -H "X-API-Key: your-snow-api-key" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "system.user",
    "content": "Automated laptop refresh notification",
    "tool_id": "snow-integration",
    "trigger_event": "asset.refresh.due"
  }'
```

## Step 8: Monitor and Observe

### Access Observability Tools
```bash
# Get Kiali URL
oc get route kiali -n istio-system -o jsonpath='{.spec.host}'

# Get Jaeger URL  
oc get route jaeger -n istio-system -o jsonpath='{.spec.host}'

# Get Grafana URL
oc get route grafana -n istio-system -o jsonpath='{.spec.host}'
```

### View Metrics
```bash
# Check request metrics
oc exec -n istio-system deployment/prometheus -- \
  promtool query instant 'istio_requests_total{destination_app="request-manager"}'

# View distributed traces in Jaeger UI
# Navigate to Jaeger UI and search for traces with service "request-manager"
```

### Check Logs
```bash
# Request Manager logs
oc logs -n llama-stack-rag -l app=request-manager -c request-manager

# Istio sidecar logs
oc logs -n llama-stack-rag -l app=request-manager -c istio-proxy

# Agent Service logs
oc logs -n llama-stack-rag -l app=agent-service -c agent-service
```

## Troubleshooting

### Common Issues

#### 1. Sidecar Not Injected
```bash
# Check namespace labeling
oc get namespace llama-stack-rag --show-labels

# Check Service Mesh member roll
oc get smmr -n istio-system -o yaml

# Manually inject if needed
oc patch deployment request-manager -n llama-stack-rag -p '{"spec":{"template":{"metadata":{"annotations":{"sidecar.istio.io/inject":"true"}}}}}'
```

#### 2. Authentication Failures
```bash
# Check JWT configuration
oc get requestauthentication -n llama-stack-rag -o yaml

# Verify token with jwt.io or:
echo "your-jwt-token" | cut -d. -f2 | base64 -d | jq .

# Check authorization policies
oc get authorizationpolicy -n llama-stack-rag -o yaml
```

#### 3. Network Connectivity Issues
```bash
# Check Service Mesh configuration
istioctl proxy-config cluster request-manager-xxxxx.llama-stack-rag

# Verify mTLS status
istioctl authn tls-check request-manager.llama-stack-rag.svc.cluster.local

# Check destination rules
oc get destinationrule -n llama-stack-rag -o yaml
```

#### 4. Performance Issues
```bash
# Check circuit breaker status
istioctl proxy-config endpoints request-manager-xxxxx.llama-stack-rag

# View rate limiting configuration
oc logs -n llama-stack-rag -l app=request-manager -c istio-proxy | grep rate_limit

# Monitor resource usage
oc top pods -n llama-stack-rag
```

## Security Best Practices

### 1. Network Policies
```bash
# Apply network policies to restrict traffic
cat <<EOF | oc apply -f -
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: request-manager-netpol
  namespace: llama-stack-rag
spec:
  podSelector:
    matchLabels:
      app: request-manager
  policyTypes:
  - Ingress
  - Egress
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          name: istio-system
    - namespaceSelector:
        matchLabels:
          name: knative-eventing
  egress:
  - to:
    - namespaceSelector:
        matchLabels:
          name: llama-stack-rag
    ports:
    - protocol: TCP
      port: 5432  # PostgreSQL
  - to: []
    ports:
    - protocol: TCP
      port: 443  # HTTPS outbound
EOF
```

### 2. Pod Security Standards
```bash
# Apply pod security standards
oc label namespace llama-stack-rag \
  pod-security.kubernetes.io/enforce=restricted \
  pod-security.kubernetes.io/audit=restricted \
  pod-security.kubernetes.io/warn=restricted
```

### 3. Secret Management
```bash
# Use External Secrets Operator for production secrets
# Or encrypt secrets at rest
oc patch secret api-keys -n llama-stack-rag -p '{"metadata":{"annotations":{"encryption.kubernetes.io/provider":"aescbc"}}}'
```

## Scaling Configuration

### Horizontal Pod Autoscaler
```bash
cat <<EOF | oc apply -f -
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: request-manager-hpa
  namespace: llama-stack-rag
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: request-manager
  minReplicas: 2
  maxReplicas: 20
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
EOF
```

This deployment guide ensures a production-ready Request Management Layer with comprehensive security, observability, and scalability features provided by OpenShift Service Mesh.
