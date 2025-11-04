# Email Integration Setup

Quick setup guide for configuring Email integration with the Self-Service Agent system.

## Overview

The Email integration provides two-way communication between users and the AI agent through email. The integration uses **IMAP** for receiving emails (polling) and **SMTP** for sending email responses. The integration uses the **Integration Defaults** system for automatic configuration and handles email processing through the Integration Dispatcher.

**How it works:**
- **Receiving**: Integration Dispatcher polls IMAP mailbox at configured interval. Leader election ensures only one pod polls (using PostgreSQL advisory locks). New emails are processed and forwarded to Request Manager via CloudEvents. User mapping is created automatically based on email address.
- **Sending**: Agent responses are sent via SMTP to user's email address. Delivery status is logged and tracked. Email threading is supported via In-Reply-To and References headers.

## Prerequisites

- Email account with SMTP (sending) and IMAP (receiving) access
- Deployed Self-Service Agent system
- Integration Dispatcher service running

## Quick Setup

### Step 1: Choose Email Provider

The system supports any email provider that supports SMTP and IMAP:

**Common Providers:**

- **Gmail**: 
  - SMTP: `smtp.gmail.com` (587/TLS) or `smtp.gmail.com` (465/SSL)
  - IMAP: `imap.gmail.com` (993/SSL)
  - Requirements: Enable IMAP in Gmail settings. Create [App Password](https://support.google.com/accounts/answer/185833) if 2FA is enabled.

- **Outlook/Office 365**: 
  - SMTP: `smtp-mail.outlook.com` (587/TLS)
  - IMAP: `outlook.office365.com` (993/SSL)
  - Requirements: Enable IMAP in Outlook settings. Use App Password if MFA is enabled.

- **Custom SMTP/IMAP**: Any provider supporting standard SMTP/IMAP protocols

### Step 2: Get Email Credentials

You'll need:
1. **SMTP Server** (for sending emails)
   - Host: `smtp.gmail.com` (example for Gmail)
   - Port: `587` (TLS) or `465` (SSL)
   - Username: Your email address
   - Password: App password or account password

2. **IMAP Server** (for receiving emails)
   - Host: `imap.gmail.com` (example for Gmail)
   - Port: `993` (SSL) or `143` (STARTTLS)
   - Username: Your email address (same as SMTP)
   - Password: App password or account password (same as SMTP)

**Note:** For Gmail, you'll need to create an [App Password](https://support.google.com/accounts/answer/185833) if 2FA is enabled.

### Step 3: Deploy with Email Configuration

#### Configuration Requirements

**Required for SMTP (Sending):**
- `smtpHost` - SMTP server hostname (e.g., `smtp.gmail.com`)
- `smtpUsername` - Email address for authentication
- `smtpPassword` - Password or app password

**Required for IMAP (Receiving):**
- `imapHost` - IMAP server hostname (e.g., `imap.gmail.com`)
- `imapUsername` - Optional, reuses `smtpUsername` if not set
- `imapPassword` - Optional, reuses `smtpPassword` if not set

**Optional with Defaults:**
- `smtpPort` - Default: `587` (use `465` for SSL/TLS, `587` for STARTTLS)
- `smtpUseTls` - Default: `true` (required for port `587`, not used for port `465`)
- `fromEmail` - Default: `noreply@selfservice.local` (sender email address)
- `fromName` - Default: `Self-Service Agent` (sender display name)
- `imapPort` - Default: `993` (use `993` for SSL/TLS, `143` for STARTTLS)
- `imapUseSsl` - Default: `true` (required for port `993`, not used for port `143`)
- `imapMailbox` - Default: `INBOX` (email folder/label to poll, e.g., `INBOX`, `SSA_TEST`)
- `imapPollInterval` - Default: `60` (seconds) - How often to check for new emails
- `imapLeaseDuration` - Default: `120` (seconds) - Leader election lease duration (should be 2x `pollInterval` to prevent multiple pods from polling simultaneously)
- `imapLeaseRenewalInterval` - Optional, defaults to `lease_duration // 2` - How often the leader renews its lease

#### Configure Email Settings

**Option 1: Using Helm Values (Recommended)**

Update `helm/values.yaml` with your email configuration:

```yaml
security:
  email:
    smtpHost: "smtp.gmail.com"
    smtpPort: "587"
    smtpUsername: "your-email@gmail.com"
    smtpPassword: "your-app-password"
    smtpUseTls: "true"
    fromEmail: "your-email@gmail.com"
    fromName: "Self-Service Agent"
    imapHost: "imap.gmail.com"
    imapPort: "993"
    imapUseSsl: "true"
    imapMailbox: "INBOX"
    imapPollInterval: "60"
    imapLeaseDuration: "120"
```

Then deploy:

```bash
make helm-install-test NAMESPACE=your-namespace
```

**Option 2: Using EXTRA_HELM_ARGS**

Alternatively, you can pass configuration directly via `EXTRA_HELM_ARGS`:

```bash
make helm-install-test NAMESPACE=your-namespace \
  EXTRA_HELM_ARGS="\
    --set-string security.email.smtpHost=smtp.gmail.com \
    --set-string security.email.smtpPort=587 \
    --set-string security.email.smtpUsername=your-email@gmail.com \
    --set-string security.email.smtpPassword=your-app-password \
    --set-string security.email.smtpUseTls=true \
    --set-string security.email.fromEmail=your-email@gmail.com \
    --set-string security.email.fromName='Self-Service Agent' \
    --set-string security.email.imapHost=imap.gmail.com \
    --set-string security.email.imapPort=993 \
    --set-string security.email.imapUseSsl=true \
    --set-string security.email.imapMailbox=INBOX \
    --set-string security.email.imapPollInterval=60 \
    --set-string security.email.imapLeaseDuration=120"
```

**Note:** 
- **Option 1 (values.yaml) is recommended** for persistent configuration and production deployments
- **Option 2 (EXTRA_HELM_ARGS)** is useful for one-off deployments, testing, or CI/CD pipelines
- Use `--set-string` for all values to ensure proper string handling
- If `imapUsername` and `imapPassword` are not set, they will reuse `smtpUsername` and `smtpPassword` respectively
- Minimum required configuration: `smtpHost`, `smtpUsername`, `smtpPassword`, and `imapHost` (if you want IMAP polling)

### Step 4: Verify Configuration

```bash
# Check Integration Dispatcher logs
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i email

# Check integration health
kubectl exec deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} -- curl -s http://localhost:8080/health
```

Look for:
- `"EMAIL"` in the `integrations_available` array
- `"Starting IMAP email polling"` in logs
- `"Became leader"` for IMAP polling (leader election)

## Integration Defaults

The Email integration automatically uses the **Integration Defaults** system:

```json
{
  "enabled": true,  // Auto-enabled if SMTP and IMAP are configured
  "priority": 2,    // Second priority (after Slack)
  "retry_count": 3,
  "retry_delay_seconds": 60,
  "config": {
    "include_agent_info": true
  }
}
```

### How It Works

1. **System checks** if email is properly configured (SMTP and IMAP credentials)
2. **Auto-enables** Email integration if health check passes
3. **All users** get Email integration by default (if email address exists as a user)
4. **Users can override** with custom configurations if needed

## User Interaction

Users can interact with the AI agent via email:

### Sending Emails

Simply send an email to the configured email address (`FROM_EMAIL` or `SMTP_USERNAME`). The system will:
- Process the email content
- Create a user mapping if needed
- Forward to the agent
- Reply with the agent's response

### Reply Threading

The system supports email threading:
- **In-Reply-To** header is used to maintain conversation context
- **References** header tracks conversation threads
- Replies maintain the same session for continuity

## Custom User Configuration

If you need to customize email behavior for specific users:

```bash
kubectl exec deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} -- curl -s -X POST http://localhost:8080/api/v1/users/user@example.com/integrations \
  -H "Content-Type: application/json" \
  -d '{
    "integration_type": "EMAIL",
    "enabled": true,
    "config": {
      "email_address": "user@example.com",
      "format": "html",
      "reply_to": "support@company.com"
    },
    "priority": 2,
    "retry_count": 5,
    "retry_delay_seconds": 30
  }'
```

## Configuration Options

| Field | Type | Description |
|-------|------|-------------|
| `email_address` | string | Recipient email address (required) |
| `format` | string | Email format: `"html"` or `"text"` (default: `"html"`) |
| `reply_to` | string | Reply-To email address (optional) |
| `include_agent_info` | boolean | Include agent information in email (default: `true`) |

## Environment Variables Reference

### SMTP Configuration (Sending)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SMTP_HOST` | **Yes** | `localhost` | SMTP server hostname (e.g., `smtp.gmail.com`) |
| `SMTP_PORT` | No | `587` | SMTP server port: `587` for STARTTLS, `465` for SSL/TLS |
| `SMTP_USERNAME` | **Yes** | - | SMTP username (your email address) |
| `SMTP_PASSWORD` | **Yes** | - | SMTP password or app password |
| `SMTP_USE_TLS` | No | `true` | Use STARTTLS (required for port `587`, not used for port `465`) |
| `FROM_EMAIL` | No | `noreply@selfservice.local` | Sender email address (shown in "From" field) |
| `FROM_NAME` | No | `Self-Service Agent` | Sender display name (shown in "From" field) |

**Note:** SMTP is required for sending emails. Minimum required: `SMTP_HOST`, `SMTP_USERNAME`, and `SMTP_PASSWORD`.

### IMAP Configuration (Receiving)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `IMAP_HOST` | **Yes** (if IMAP polling) | - | IMAP server hostname (e.g., `imap.gmail.com`) |
| `IMAP_PORT` | No | `993` | IMAP server port: `993` for SSL/TLS, `143` for STARTTLS |
| `IMAP_USERNAME` | No | `SMTP_USERNAME` | IMAP username (reuses SMTP credentials if not set) |
| `IMAP_PASSWORD` | No | `SMTP_PASSWORD` | IMAP password (reuses SMTP credentials if not set) |
| `IMAP_USE_SSL` | No | `true` | Use SSL/TLS (required for port `993`, not used for port `143`) |
| `IMAP_MAILBOX` | No | `INBOX` | Email folder/label to poll (e.g., `INBOX`, `SSA_TEST`) |
| `IMAP_POLL_INTERVAL` | No | `60` | How often to check for new emails (seconds) |
| `IMAP_LEASE_DURATION` | No | `120` | Leader election lease duration (seconds). Should be 2x `pollInterval` to prevent multiple pods from polling simultaneously |
| `IMAP_LEASE_RENEWAL_INTERVAL` | No | `lease_duration // 2` | How often the leader renews its lease (seconds). Prevents lease expiration during long polling operations |

**Note:** IMAP is optional and only required if you want email polling (receiving emails). Minimum required for IMAP polling: `IMAP_HOST` and `SMTP_USERNAME` (IMAP credentials default to SMTP credentials if not set).

## Testing

### Test Email Delivery

```bash
# Send a test email via the delivery endpoint
kubectl exec deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} -- curl -s -X POST http://localhost:8080/deliver \
  -H "Content-Type: application/json" \
  -d '{
    "request_id": "test-123",
    "session_id": "test-session-123",
    "user_id": "user@example.com",
    "agent_id": "routing-agent",
    "subject": "Test Email",
    "content": "This is a test email from the Self-Service Agent.",
    "template_variables": {}
  }'
```

### Test User Configuration

```bash
kubectl exec deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} -- curl -s http://localhost:8080/api/v1/users/user@example.com/integration-defaults
```

### Monitor Email Processing

```bash
# Watch Integration Dispatcher logs
kubectl logs -f deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default}

# Look for email processing
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i "email\|imap"

# Check for successful delivery
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i "delivered\|sent"
```

## Troubleshooting

### Common Issues

#### Email integration not working
- Check if Email is enabled in defaults: `GET /api/v1/integration-defaults`
- Verify SMTP and IMAP credentials are configured
- Check Integration Dispatcher logs for errors
- Verify email account has IMAP enabled (for receiving)

#### User not receiving emails
- Check user's effective configuration: `GET /api/v1/users/{user_id}/integration-defaults`
- Verify user has Email integration enabled
- Verify user's email address exists in the system
- Check delivery logs: `GET /api/v1/users/{user_id}/deliveries`
- Verify SMTP credentials are correct
- Check email provider's sending limits/quotas

#### Emails not being received (IMAP polling)
- Verify IMAP credentials are correct
- Check that IMAP is enabled on the email account
- Verify the mailbox exists (`IMAP_MAILBOX`)
- Check logs for IMAP connection errors
- Verify leader election is working (only one pod should poll)

#### SMTP authentication errors
- Verify SMTP username and password are correct
- For Gmail, ensure you're using an App Password (not account password)
- Check that "Less secure app access" is enabled (if using Gmail without 2FA)
- Verify SMTP port matches TLS/SSL setting

#### IMAP connection errors
- Verify IMAP host and port are correct
- Check that IMAP access is enabled on the email account
- Verify SSL/TLS settings match the port (993 = SSL, 143 = STARTTLS)
- Check firewall/network rules allow IMAP access

### Debug Commands

```bash
# Check integration health
kubectl exec deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} -- curl -s http://localhost:8080/health

# Check email-specific health
kubectl exec deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} -- curl -s http://localhost:8080/health/detailed | jq '.integrations_available'

# Check user delivery history
kubectl exec deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} -- curl -s http://localhost:8080/api/v1/users/user@example.com/deliveries | jq

# Reset user to defaults
kubectl exec deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} -- curl -s -X POST http://localhost:8080/api/v1/users/user@example.com/integration-defaults/reset

# Check Integration Dispatcher logs for email events
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i email
```

### Email Processing Debugging

The Integration Dispatcher logs detailed information about email processing:

```bash
# Watch for IMAP polling events
kubectl logs -f deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i "imap\|polling"

# Check for email processing errors
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i "email.*error\|failed.*email"

# Check leader election status
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i "leader\|lease"
```

## Best Practices

1. **Use App Passwords** - For Gmail and other providers, use App Passwords instead of account passwords
2. **Configure IMAP Polling** - Set appropriate polling interval based on your needs (60s default)
3. **Monitor Leader Election** - Ensure only one pod is polling IMAP (leader election)
4. **Test After Setup** - Verify the integration works end-to-end (send and receive)
5. **Configure Mailbox** - Use a dedicated mailbox/folder for testing (e.g., `SSA_TEST`)
6. **Monitor Delivery Logs** - Check for failed deliveries
7. **Configure Retry Settings** - Based on your email provider's reliability
8. **Use Dedicated Email Account** - Consider using a dedicated email account for the agent
9. **Set Appropriate FROM_EMAIL** - Use a recognizable email address for users
10. **Test Email Threading** - Verify reply threading works correctly

## Security Considerations

- **App Passwords**: Use App Passwords instead of account passwords when possible
- **Credentials Storage**: Store email credentials securely in Kubernetes secrets
- **TLS/SSL**: Always use TLS/SSL for SMTP and IMAP connections
- **Email Validation**: System validates email addresses before sending
- **Rate Limiting**: Built-in rate limiting prevents abuse
- **Deduplication**: Email messages are deduplicated to prevent duplicate processing

## Related Documentation

- [Integration Guide](INTEGRATION_GUIDE.md) - Complete integration and request management guide
- [API Reference](../docs/API_REFERENCE.md) - API endpoints and usage
- [Slack Setup Guide](SLACK_SETUP.md) - Slack integration setup (similar to email)
