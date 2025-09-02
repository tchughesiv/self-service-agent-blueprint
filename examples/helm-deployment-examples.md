# Helm Deployment Examples with Request Management Layer

This document provides examples of deploying the Self-Service Agent Blueprint with various configurations of the Request Management Layer.

## Prerequisites

1. **OpenShift Cluster** with appropriate permissions
2. **OpenShift Service Mesh Operator** installed (for Service Mesh features)
3. **OpenShift Serverless Operator** installed (for Knative features)
4. **Helm 3.x** installed
5. **Container images** built and pushed to registry

## Basic Deployment (No Request Management Layer)

Deploy just the core Self-Service Agent components:

```bash
make helm-install NAMESPACE=my-namespace
```

This deploys:
- Asset Manager
- Llama Stack with PostgreSQL persistence
- MCP Servers (Employee Info, ServiceNow)

## Request Management Layer with Knative Only

Enable the Request Management Layer with Knative eventing but no Service Mesh:

```bash
make helm-install \
  NAMESPACE=my-namespace \
  REQUEST_MANAGEMENT=true \
  SERVICE_MESH=false \
  KNATIVE_EVENTING=true
```

This adds:
- Request Manager (Knative Service)
- Agent Service (Knative Service)
- Knative Broker and Triggers
- Internal-only networking

## Full Request Management Layer with Service Mesh

Enable all features including OpenShift Service Mesh:

```bash
make helm-install \
  NAMESPACE=my-namespace \
  REQUEST_MANAGEMENT=true \
  SERVICE_MESH=true \
  KNATIVE_EVENTING=true \
  API_GATEWAY_HOST=api.selfservice.apps.cluster.local \
  SLACK_SIGNING_SECRET=your-slack-secret \
  SNOW_API_KEY=your-snow-api-key \
  HR_API_KEY=your-hr-api-key
```

This deploys the complete architecture:
- All core components
- Request Manager with Service Mesh security
- Agent Service with Service Mesh integration
- Istio Gateway with HTTPS/TLS
- JWT authentication and authorization policies
- API key management for tool integrations
- Distributed tracing and metrics
- Knative eventing infrastructure

## Production Deployment with External Secrets

For production, use external secret management:

```bash
# Create TLS certificate secret
kubectl create secret tls selfservice-tls-secret \
  --cert=tls.crt \
  --key=tls.key \
  -n my-namespace

# Deploy with external secrets
make helm-install \
  NAMESPACE=my-namespace \
  REQUEST_MANAGEMENT=true \
  SERVICE_MESH=true \
  KNATIVE_EVENTING=true \
  API_GATEWAY_HOST=api.selfservice.apps.cluster.local \
  EXTRA_HELM_ARGS="--set security.apiKeys.snowIntegration= --set security.slack.signingSecret="

# Update secrets after deployment
kubectl patch secret api-keys -n my-namespace \
  --patch='{"data":{"snow-integration":"'$(echo -n "your-real-snow-api-key" | base64)'"}}'

kubectl patch secret slack-signing-secret -n my-namespace \
  --patch='{"data":{"signing-secret":"'$(echo -n "your-real-slack-secret" | base64)'"}}'
```

## Multi-Environment Configuration

### Development Environment
```bash
make helm-install \
  NAMESPACE=dev-selfservice \
  REQUEST_MANAGEMENT=true \
  SERVICE_MESH=false \
  KNATIVE_EVENTING=true \
  LLM=llama-3-2-1b-instruct \
  SAFETY=llama-guard-3-1b
```

### Staging Environment
```bash
make helm-install \
  NAMESPACE=staging-selfservice \
  REQUEST_MANAGEMENT=true \
  SERVICE_MESH=true \
  KNATIVE_EVENTING=true \
  API_GATEWAY_HOST=api-staging.selfservice.apps.cluster.local \
  LLM=llama-3-2-3b-instruct \
  SAFETY=llama-guard-3-1b
```

### Production Environment
```bash
make helm-install \
  NAMESPACE=prod-selfservice \
  REQUEST_MANAGEMENT=true \
  SERVICE_MESH=true \
  KNATIVE_EVENTING=true \
  API_GATEWAY_HOST=api.selfservice.apps.cluster.local \
  LLM=llama-3-1-70b-instruct \
  SAFETY=llama-guard-3-8b \
  SLACK_SIGNING_SECRET=${SLACK_SECRET} \
  SNOW_API_KEY=${SNOW_API_KEY} \
  HR_API_KEY=${HR_API_KEY}
```

## Custom Configuration with values.yaml

Create a custom values file for complex configurations:

```yaml
# custom-values.yaml
requestManagement:
  enabled: true
  serviceMesh:
    enabled: true
    gateway:
      hosts:
        - "api.selfservice.apps.cluster.local"
        - "chat.selfservice.apps.cluster.local"
      tls:
        secretName: "custom-tls-secret"
  
  requestManager:
    resources:
      requests:
        memory: "512Mi"
        cpu: "200m"
      limits:
        memory: "1Gi"
        cpu: "1000m"
    autoscaling:
      minScale: 2
      maxScale: 20
      target: 5
  
  agentService:
    resources:
      requests:
        memory: "1Gi"
        cpu: "500m"
      limits:
        memory: "2Gi"
        cpu: "2000m"

security:
  jwt:
    issuers:
      - issuer: "https://sso.company.com/auth/realms/employees"
        jwksUri: "https://sso.company.com/auth/realms/employees/protocol/openid-connect/certs"
        audience: "selfservice-api"
```

Deploy with custom values:

```bash
make helm-install \
  NAMESPACE=my-namespace \
  EXTRA_HELM_ARGS="-f custom-values.yaml"
```

## Verification and Testing

After deployment, verify the installation:

```bash
# Check deployment status
make helm-status NAMESPACE=my-namespace

# Test health endpoints
kubectl port-forward svc/self-service-agent-request-manager 8080:80 -n my-namespace
curl http://localhost:8080/health

# Test via Service Mesh Gateway (if enabled)
curl -k https://api.selfservice.apps.cluster.local/health

# Check Service Mesh configuration (if enabled)
oc get gateway,virtualservice,authorizationpolicy -n my-namespace

# Check Knative resources (if enabled)
oc get broker,trigger,ksvc -n my-namespace

# View deployment notes
helm get notes self-service-agent -n my-namespace
```

## Integration Testing

### Web Request Test (with JWT)
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

### Tool Request Test (with API Key)
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

### Session Management Test
```bash
# Create a session
curl -X POST https://api.selfservice.apps.cluster.local/api/v1/sessions \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "test.user",
    "integration_type": "web"
  }'

# Get session info
SESSION_ID="returned-session-id"
curl https://api.selfservice.apps.cluster.local/api/v1/sessions/$SESSION_ID \
  -H "Authorization: Bearer $TOKEN"
```

## Troubleshooting

### Common Issues

1. **Service Mesh not working**:
   ```bash
   # Check if Service Mesh is installed
   oc get smcp -n istio-system
   oc get smmr -n istio-system
   
   # Check pod injection
   oc get pods -n my-namespace -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.containers[*].name}{"\n"}{end}'
   ```

2. **Knative services not starting**:
   ```bash
   # Check Knative Serving
   oc get knativeserving -n knative-serving
   
   # Check service logs
   oc logs -l app=self-service-agent-request-manager -n my-namespace
   ```

3. **Authentication failures**:
   ```bash
   # Check JWT configuration
   oc get requestauthentication -n my-namespace -o yaml
   
   # Check authorization policies
   oc get authorizationpolicy -n my-namespace -o yaml
   ```

4. **Database connection issues**:
   ```bash
   # Check PostgreSQL
   oc get pods -l app=pgvector -n my-namespace
   oc logs -l app=pgvector -n my-namespace
   
   # Test database connection
   oc exec -it deployment/pgvector -n my-namespace -- psql -U postgres -d llama_agents -c "SELECT 1;"
   ```

## Monitoring and Observability

### Service Mesh Observability (if enabled)
```bash
# Get Kiali URL
oc get route kiali -n istio-system

# Get Jaeger URL
oc get route jaeger -n istio-system

# Get Grafana URL
oc get route grafana -n istio-system
```

### Application Metrics
```bash
# Check Prometheus metrics
oc exec -n istio-system deployment/prometheus -- \
  promtool query instant 'istio_requests_total{destination_app="self-service-agent-request-manager"}'
```

### Logs
```bash
# Request Manager logs
oc logs -l app=self-service-agent-request-manager -n my-namespace

# Agent Service logs
oc logs -l app=self-service-agent-agent-service -n my-namespace

# Istio sidecar logs (if Service Mesh enabled)
oc logs -l app=self-service-agent-request-manager -c istio-proxy -n my-namespace
```

This comprehensive deployment guide covers all the major configuration options and operational procedures for the Request Management Layer integration.
