# Knative TLS Status Summary

## Current Situation

✅ **Changes Reverted**: All modifications outside `tommy` namespace have been reverted:
- `external-domain-tls: Disabled` (back to default)
- `certificate-class` removed (back to default)
- `issuerRef` removed (back to default)

✅ **Certificates Ready**: Our cert-manager certificates are successfully issued:
```bash
NAME                                             READY   SECRET
self-service-agent-agent-service-cert            True    self-service-agent-agent-service-tls
self-service-agent-dead-letter-sink-cert         True    self-service-agent-dead-letter-sink-tls
self-service-agent-integration-dispatcher-cert   True    self-service-agent-integration-dispatcher-tls
self-service-agent-request-manager-cert          True    self-service-agent-request-manager-tls
```

❌ **Knative Services Still Uninitialized**: Despite having valid certificates:
```bash
NAME                                        READY     REASON
self-service-agent-agent-service            Unknown   Uninitialized
self-service-agent-dead-letter-sink         Unknown   Uninitialized
self-service-agent-integration-dispatcher   Unknown   Uninitialized
self-service-agent-request-manager          Unknown   Uninitialized
```

## Root Cause Analysis

**The Issue**: Knative is creating its own certificates (`route-*`) that remain `False`, while our working cert-manager certificates exist separately. Knative doesn't know about our certificates.

**Available Options**:

### Option 1: Use Cluster-Local Services (Recommended)
- Set `serviceVisibility: "cluster-local"` to bypass external TLS entirely
- Services become accessible via internal cluster domains
- No external TLS complexity
- **Pros**: Simple, works immediately
- **Cons**: No external access

### Option 2: Enable External TLS (Current Attempt) 
- Keep `serviceVisibility: "external"` 
- Would require enabling `external-domain-tls` globally (outside tommy namespace)
- **Pros**: External HTTPS access
- **Cons**: Requires global Knative configuration changes

### Option 3: Use DomainMapping with Local Domains
- Map services to `.local` domains that don't require external TLS
- Keep services cluster-local but with custom domains
- **Pros**: Custom domains without external TLS complexity
- **Cons**: Still internal-only access

## Current Helm Configuration

The solution is **fully configurable** via `values.yaml`:

```yaml
requestManagement:
  knative:
    # Option 1: Cluster-local (bypasses TLS issues)
    serviceVisibility: "cluster-local"
    certificates:
      enabled: false
    
    # Option 2: External with TLS (requires global config changes)  
    serviceVisibility: "external"
    certificates:
      enabled: true
      issuer:
        name: "letsencrypt-production"
```

## Recommendation

For immediate resolution without global changes:
1. Set `serviceVisibility: "cluster-local"`
2. Set `certificates.enabled: false`
3. Services will be accessible via internal cluster domains
4. All Knative services should become "Ready"

This provides a **working solution within the tommy namespace only**.
