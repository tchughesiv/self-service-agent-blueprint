# Cert-Manager Implementation for Knative TLS

This document describes the cert-manager implementation for resolving Knative service TLS issues.

## Implementation Summary

✅ **Successfully implemented cert-manager integration** using the existing cert-manager Operator for Red Hat OpenShift.

### What Was Implemented

1. **Certificate Resources** (`helm/templates/cert-manager.yaml`):
   - 4 Certificate resources for each Knative service
   - Uses existing `letsencrypt-production` ClusterIssuer
   - Configured for OpenShift domain pattern

2. **Updated Configuration** (`values.yaml`):
   ```yaml
   requestManagement:
     knative:
       serviceVisibility: "external"  # Changed from cluster-local
       certificates:
         enabled: true
         issuer:
           name: "letsencrypt-production"  # Uses existing issuer
         domains:
           agentService: "self-service-agent-agent-service-tommy.apps.ai-dev02.kni.syseng.devcluster.openshift.com"
           # ... other services
   ```

3. **Helm-Configurable**: Easy to enable/disable certificate management
   ```yaml
   # Enable cert-manager certificates
   certificates:
     enabled: true
   
   # Disable cert-manager certificates  
   certificates:
     enabled: false
   ```

## Current Status

### ✅ Working Components
- **cert-manager Operator**: Already installed and functional
- **ClusterIssuer**: `letsencrypt-production` exists and ready
- **Certificate Resources**: All 4 certificates created and approved
- **ACME Orders**: Let's Encrypt orders created successfully
- **ACME Challenges**: HTTP-01 challenges initiated

### 🔄 In Progress
- **Certificate Issuance**: Let's Encrypt validating domain ownership via HTTP-01 challenges
- **Domain Validation**: Challenges in "pending" state waiting for validation

### 📋 Certificate Status
```bash
kubectl get certificates -n tommy
NAME                                             READY   SECRET
self-service-agent-agent-service-cert            False   self-service-agent-agent-service-tls
self-service-agent-dead-letter-sink-cert         False   self-service-agent-dead-letter-sink-tls
self-service-agent-integration-dispatcher-cert   False   self-service-agent-integration-dispatcher-tls
self-service-agent-request-manager-cert          False   self-service-agent-request-manager-tls
```

## How It Works

1. **Certificate Creation**: Helm deploys Certificate resources targeting OpenShift domains
2. **ACME Process**: cert-manager initiates Let's Encrypt ACME protocol
3. **HTTP-01 Challenges**: Let's Encrypt validates domain ownership
4. **Certificate Issuance**: Upon successful validation, TLS certificates are issued
5. **Secret Storage**: Certificates stored in Kubernetes secrets
6. **Knative Integration**: Knative automatically uses certificates for TLS termination

## Expected Resolution Timeline

- **HTTP-01 Validation**: 1-5 minutes (depends on DNS propagation and ingress accessibility)
- **Certificate Issuance**: 30 seconds after successful validation
- **Knative Route Ready**: 1-2 minutes after certificate availability

## Monitoring Commands

```bash
# Check certificate status
kubectl get certificates -n tommy

# Check certificate details
kubectl describe certificate self-service-agent-agent-service-cert -n tommy

# Check ACME challenges
kubectl get challenges -n tommy

# Check certificate requests
kubectl get certificaterequests -n tommy

# Check Knative service status
kubectl get ksvc -n tommy
```

## Configuration Options

### Production Configuration
```yaml
requestManagement:
  knative:
    serviceVisibility: "external"
    certificates:
      enabled: true
      issuer:
        name: "letsencrypt-production"
```

### Development/Testing Configuration  
```yaml
requestManagement:
  knative:
    serviceVisibility: "cluster-local"  # Skip external TLS
    certificates:
      enabled: false
```

## Expected Outcome

Once certificates are issued:
- ✅ Knative services will show "Ready" status instead of "Unknown/Uninitialized"
- ✅ External HTTPS URLs will be accessible with valid TLS certificates
- ✅ "TLSNotEnabled external-domain-tls" errors will be resolved
- ✅ Load balancer issues will be resolved through proper TLS configuration

The cert-manager approach provides a **production-ready, automated solution** for Knative TLS certificate management on OpenShift.
