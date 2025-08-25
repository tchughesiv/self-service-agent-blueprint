# Infinispan Session Persistence Configuration

This document describes how to configure and use Infinispan for session persistence with the llama-stack deployment.

## Overview

The self-service-agent blueprint now supports persistent session storage using Infinispan, a distributed in-memory data grid. This ensures that agent sessions persist across pod restarts and can be shared across multiple replicas.

The Infinispan deployment uses the default server configuration for maximum compatibility with Infinispan 15.x. Session caches are created dynamically by the application, avoiding complex XML configuration issues.

## Configuration

### Enabling Infinispan

To enable Infinispan session persistence, set the following in your `values.yaml`:

```yaml
infinispan:
  enabled: true
```

### Infinispan Configuration Options

The following configuration options are available in `values.yaml`:

```yaml
infinispan:
  enabled: true
  
  # Container image configuration
  image:
    repository: quay.io/infinispan/server
    tag: "15.0"
    pullPolicy: IfNotPresent
  
  # Service configuration
  service:
    type: ClusterIP
    port: 11222
  
  # Resource limits and requests
  resources:
    requests:
      memory: "512Mi"
      cpu: "500m"
    limits:
      memory: "1Gi" 
      cpu: "1000m"
  
  # Persistent storage for session data
  persistence:
    enabled: false    # Disabled due to file-store deprecation in Infinispan 15.x
    storageClass: ""  # Use default storage class
    size: "8Gi"
  
  # Cache configuration
  config:
    clustering:
      transport: tcp
    
    caches:
      sessions:
        mode: "SYNC"      # Synchronization mode: SYNC or ASYNC
        owners: 2         # Number of owners for distributed cache
        segments: 256     # Number of segments for distribution
        expiration:
          lifespan: 3600000  # 1 hour in milliseconds
          maxIdle: 1800000   # 30 minutes in milliseconds
        persistence:
          passivation: false
          stores:
            - type: "file"
              shared: false
              preload: true
              purge: false
              fetchState: true
```

### Llama-Stack Integration

The llama-stack is automatically configured to use Infinispan when enabled:

```yaml
llama-stack:
  extraEnvVars:
    - name: SESSION_PERSISTENCE_ENABLED
      value: "true"
    - name: SESSION_PERSISTENCE_TYPE
      value: "infinispan"
    - name: INFINISPAN_HOST
      value: "{{ .Release.Name }}-infinispan"
    - name: INFINISPAN_PORT
      value: "11222"
    - name: INFINISPAN_CACHE_NAME
      value: "sessions"
    - name: INFINISPAN_USERNAME
      value: "admin"
    - name: INFINISPAN_PASSWORD
      value: "password"
```

## Deployment

### Prerequisites

1. Kubernetes cluster with persistent volume support (if persistence is enabled)
2. Helm 3.x

Note: Infinispan is deployed using built-in Kubernetes templates, so no external chart dependencies are required.

### Deploy with Infinispan

Deploy the chart with Infinispan enabled:

```bash
helm install my-release ./helm --set infinispan.enabled=true
```

Or upgrade an existing deployment:

```bash
helm upgrade my-release ./helm --set infinispan.enabled=true
```

## Monitoring and Operations

### Health Checks

Infinispan includes built-in health checks:

- **Liveness Probe**: `/rest/v2/cache-managers/default/health/status`
- **Readiness Probe**: `/rest/v2/cache-managers/default/health/status`

### Accessing Infinispan Console

If you need to access the Infinispan web console for debugging:

```bash
kubectl port-forward svc/my-release-infinispan 11222:11222
```

Then access: `http://localhost:11222/console`

Default credentials:
- Username: `admin`
- Password: `password`

### Creating Session Cache Dynamically

Since we use the default configuration, you can create the session cache via REST API:

```bash
# Create a distributed cache for sessions
curl -X POST \
  -H "Content-Type: application/json" \
  -d '{
    "distributed-cache": {
      "mode": "SYNC",
      "owners": 2,
      "segments": 256,
      "expiration": {
        "lifespan": 3600000,
        "max-idle": 1800000
      },
      "memory": {
        "max-count": 10000,
        "when-full": "REMOVE"
      }
    }
  }' \
  http://admin:password@localhost:11222/rest/v2/caches/sessions
```

### Cache Statistics

You can view cache statistics via the REST API:

```bash
kubectl exec -it deployment/my-release-infinispan -- \
  curl -u admin:password http://localhost:11222/rest/v2/caches/sessions?action=stats
```

## Session Configuration

### Session Expiration

Sessions are configured with the following expiration policies:

- **Lifespan**: 1 hour (3,600,000 ms) - Maximum time a session can exist
- **Max Idle**: 30 minutes (1,800,000 ms) - Maximum time a session can be inactive

### Persistence

**Note**: Persistence is currently disabled by default due to file-store deprecation in Infinispan 15.x.

**In-Memory Mode (Current Default)**:
- Session data is stored in memory across cluster nodes
- Sessions survive individual pod failures due to distributed caching (2 owners per entry)
- Sessions are lost if the entire Infinispan cluster is restarted
- Better performance due to no disk I/O

**Persistence Options (Advanced)**:
If you need persistence, you can enable experimental `soft-index-file-store`:
```yaml
infinispan:
  persistence:
    enabled: true
```

When persistence is enabled:
- Session data survives complete cluster restarts
- Uses `soft-index-file-store` for file-based persistence
- Requires persistent volumes for data storage

## Scaling Considerations

### Horizontal Scaling

- Infinispan uses distributed caching with 2 owners per cache entry
- Sessions are automatically distributed across available nodes
- Adding more Infinispan replicas improves fault tolerance

### Vertical Scaling

Adjust resource limits based on your session storage needs:

```yaml
infinispan:
  resources:
    requests:
      memory: "1Gi"    # Increase for more sessions
      cpu: "1000m"
    limits:
      memory: "2Gi"
      cpu: "2000m"
```

## Security

### Authentication

Basic authentication is configured via environment variables:
- Username: `admin`
- Password: `password`

**Note**: The current configuration uses the default Infinispan server setup for maximum compatibility with Infinispan 15.x. Session caches are created dynamically. For production deployments, consider:
1. Using Kubernetes secrets for credentials
2. Enabling TLS/SSL encryption
3. Implementing network policies for additional security

### Network Security

- Infinispan service is exposed only within the cluster (ClusterIP)
- Communication between llama-stack and Infinispan is internal
- Consider network policies for additional isolation

## Troubleshooting

### Common Issues

1. **Infinispan startup errors with "illegal value" for mode**
   - **Error**: `ISPN000687: Attribute 'mode' of element 'distributed-cache' has an illegal value 'distributed'`
   - **Solution**: The `mode` attribute expects synchronization values (`SYNC` or `ASYNC`), not cache types
   - **Fix**: Ensure `mode: "SYNC"` is set in values.yaml, not `mode: "distributed"`

2. **File store configuration errors**
   - **Error**: `ISPN000622: Element 'file-store'/'single-file-store' has been removed with no replacement`
   - **Solution**: In Infinispan 15.x, traditional file stores have been deprecated/removed
   - **Current Fix**: Persistence is disabled by default (`persistence.enabled: false`)
   - **Alternative**: Use `soft-index-file-store` if persistence is required (experimental)

3. **Server configuration errors**
   - **Error**: `Cannot invoke "ServerConfigurationBuilder.transport()" because "serverBuilder" is null`
   - **Solution**: Infinispan 15.x requires proper server XML configuration structure
   - **Fix**: Updated to use proper `<server>` root element with required sections

4. **XML parsing errors**
   - **Error**: `Unexpected element 'null' encountered` or `ConfigurationReaderException`
   - **Solution**: Custom XML configurations can be problematic with Infinispan 15.x
   - **Fix**: Use default Infinispan configuration and create caches dynamically via REST API

5. **Sessions not persisting**
   - Check if Infinispan pods are running: `kubectl get pods -l app.kubernetes.io/component=infinispan`
   - Verify cache configuration in ConfigMap
   - Check llama-stack environment variables

6. **Storage issues**
   - Verify PVC is bound: `kubectl get pvc`
   - Check available storage in cluster
   - Review storage class configuration

7. **Connection issues**
   - Verify service endpoints: `kubectl get endpoints`
   - Check network connectivity between pods
   - Review DNS resolution

### Logs

Check Infinispan logs:
```bash
kubectl logs deployment/my-release-infinispan
```

Check llama-stack logs for session-related errors:
```bash
kubectl logs deployment/my-release
```

## Migration

### From In-Memory Sessions

When migrating from in-memory sessions to Infinispan:

1. Sessions will be lost during the migration
2. Users may need to re-authenticate
3. Consider scheduling maintenance windows

### Backup and Restore

To backup session data:

```bash
kubectl exec -it deployment/my-release-infinispan -- \
  tar -czf /tmp/sessions-backup.tar.gz /opt/infinispan/server/data
```

## Performance Tuning

### JVM Options

For high-load scenarios, consider tuning JVM options:

```yaml
infinispan:
  extraEnvVars:
    - name: JAVA_OPTS
      value: "-Xms1g -Xmx2g -XX:+UseG1GC"
```

### Cache Configuration

Adjust cache parameters based on usage:

```yaml
infinispan:
  config:
    caches:
      sessions:
        owners: 3          # More owners = better fault tolerance
        segments: 512      # More segments = better distribution
```
