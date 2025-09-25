# Slack Integration Setup

Quick setup guide for configuring Slack integration with the Self-Service Agent system.

## Overview

The Slack integration provides two-way communication between users and the AI agent through Slack channels, DMs, and slash commands. The integration uses the **Integration Defaults** system for automatic configuration and handles events through the Integration Dispatcher.

## Prerequisites

- Slack workspace with admin permissions
- Deployed Self-Service Agent system
- Integration Dispatcher route URL

## Quick Setup

### Step 1: Create Slack App

1. Go to [Slack API Apps](https://api.slack.com/apps)
2. Click **"Create New App"**
3. Choose **"From an app manifest"**
4. Select your workspace
5. Copy and paste the contents of [`slack-app-manifest.json`](slack-app-manifest.json)
6. Click **"Next" → "Create"**

### Step 2: Get Integration Dispatcher Route

```bash
kubectl get route -n ${NAMESPACE:-default} | grep integration-dispatcher
```

The URL will be: `https://self-service-agent-integration-dispatcher-${NAMESPACE}.${DOMAIN}`

### Step 3: Configure Request URLs

After creating the app, update the placeholder URLs:

1. **Event Subscriptions**:
   - Update Request URL to: `https://your-integration-dispatcher-route/slack/events`
   - Click **"Save Changes"**

2. **Interactivity & Shortcuts**:
   - Update Request URL to: `https://your-integration-dispatcher-route/slack/interactive`
   - Click **"Save Changes"**

### Step 4: Install App

1. Go to **"Install App"**
2. Click **"Install to Workspace"**
3. Authorize the app

### Step 5: Get Credentials

1. **Bot User OAuth Token** (starts with `xoxb-`)
2. **Signing Secret** (from Basic Information)

### Step 6: Deploy with Slack Configuration

```bash
# Set credentials as environment variables
export SLACK_BOT_TOKEN="xoxb-your-actual-bot-token"
export SLACK_SIGNING_SECRET="your-actual-signing-secret"

# Deploy with Slack integration enabled
make helm-install-dev NAMESPACE=your-namespace
```

Or let the Makefile prompt for credentials:

```bash
ENABLE_SLACK=true make helm-install-dev NAMESPACE=your-namespace
```

### Step 7: Restart Integration Dispatcher

```bash
kubectl rollout restart deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default}
```

### Step 8: Verify Configuration

```bash
# Check Integration Dispatcher logs
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i slack

# Check integration health
curl http://localhost:8080/health
```

Look for `"SLACK"` in the `integrations_available` array.

## Integration Defaults

The Slack integration automatically uses the **Integration Defaults** system:

```json
{
  "enabled": true,  // Auto-enabled if Slack is configured
  "priority": 1,    // Highest priority (delivered first)
  "retry_count": 3,
  "retry_delay_seconds": 60,
  "config": {
    "thread_replies": false,
    "mention_user": false,
    "include_agent_info": true
  }
}
```

### How It Works

1. **System checks** if Slack is properly configured (bot token and signing secret)
2. **Auto-enables** Slack integration if health check passes
3. **All users** get Slack integration by default
4. **Users can override** with custom configurations if needed

## User Interaction

Users can interact with the AI agent in multiple ways:

### Direct Messages
Send a DM to the bot for private conversations.

### Slash Commands
Use `/agent [your message]` in any channel.

### @Mentions
Mention the bot in channels: `@Self-Service Agent help me`

### Interactive Buttons
Click buttons in agent responses for quick actions.

## Custom User Configuration

If you need to customize Slack behavior for specific users:

```bash
curl -X POST http://localhost:8080/api/v1/users/john.doe/integrations \
  -H "Content-Type: application/json" \
  -d '{
    "integration_type": "SLACK",
    "enabled": true,
    "config": {
      "user_email": "john.doe@company.com",
      "thread_replies": true,
      "mention_user": true,
      "include_agent_info": true
    },
    "priority": 1,
    "retry_count": 5,
    "retry_delay_seconds": 30
  }'
```

## Configuration Options

| Field | Type | Description |
|-------|------|-------------|
| `user_email` | string | User's email address for Slack user lookup |
| `slack_user_id` | string | Direct Slack user ID for DM channel |
| `channel_id` | string | Specific channel ID for responses |
| `thread_replies` | boolean | Reply in thread instead of new message |
| `mention_user` | boolean | Mention user in responses |
| `include_agent_info` | boolean | Include agent information in responses |

## Slack App Manifest

The system includes a pre-configured Slack app manifest (`slack-app-manifest.json`) with the following features:

### OAuth & Permissions
- **Bot Token Scopes**: `app_mentions:read`, `channels:history`, `chat:write`, `commands`, `groups:history`, `im:history`, `im:read`, `im:write`, `mpim:history`, `mpim:read`, `mpim:write`, `users:read`

### Event Subscriptions
- **Bot Events**: `app_mention`, `message.channels`, `message.groups`, `message.im`, `message.mpim`

### Slash Commands
- **Command**: `/agent`
- **Request URL**: `https://your-integration-dispatcher-route/slack/events`
- **Short Description**: "Interact with the self-service agent"
- **Usage Hint**: "[your message]"

### Interactivity & Shortcuts
- **Request URL**: `https://your-integration-dispatcher-route/slack/interactive`

## Testing

### Testing Approaches

There are different ways to test Slack integration depending on what you want to verify:

1. **Integration Dispatcher `/deliver`** - Tests response delivery TO Slack
2. **Request Manager `/api/v1/requests/slack`** - Tests internal request processing (used by Integration Dispatcher)
3. **Integration Dispatcher `/slack/events`** - Tests actual Slack event handling (real Slack flow)

### Test User Configuration
```bash
curl http://localhost:8081/api/v1/users/U09E98M70JK/integration-defaults
```

### Monitor Delivery
```bash
# Watch Integration Dispatcher logs
kubectl logs -f deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default}

# Look for successful delivery
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i "delivered to slack"
```

## Troubleshooting

### Common Issues

#### Slack integration not working
- Check if Slack is enabled in defaults: `GET /api/v1/integration-defaults`
- Verify bot token and signing secret are configured
- Check Integration Dispatcher logs for errors

#### User not receiving messages
- Check user's effective configuration: `GET /api/v1/users/{user_id}/integration-defaults`
- Verify user has Slack integration enabled
- Check delivery logs: `GET /api/v1/users/{user_id}/deliveries`

#### App not responding to events
- Verify request URLs are correct in Slack app settings
- Check that Integration Dispatcher is accessible
- Verify signing secret matches

#### Bot not responding to DMs
- Ensure the bot is invited to the channel or DM
- Check that the bot has the necessary permissions
- Verify the user's Slack configuration includes `user_email` or `slack_user_id`

### Debug Commands

```bash
# Check integration health
curl http://localhost:8080/health

# Check Slack-specific health
curl http://localhost:8080/health | jq '.integrations_available'

# Check user delivery history
curl http://localhost:8081/api/v1/users/U09E98M70JK/deliveries

# Reset user to defaults
curl -X POST http://localhost:8081/api/v1/users/U09E98M70JK/integration-defaults/reset

# Check Integration Dispatcher logs for Slack events
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i slack
```

### Slack Event Debugging

The Integration Dispatcher logs detailed information about Slack events:

```bash
# Watch for Slack events
kubectl logs -f deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i "slack event"

# Check for signature verification issues
kubectl logs deployment/self-service-agent-integration-dispatcher -n ${NAMESPACE:-default} | grep -i "signature"
```

## Best Practices

1. **Use Integration Defaults** - Let the system handle most users automatically
2. **Test After Setup** - Verify the integration works end-to-end
3. **Monitor Delivery Logs** - Check for failed deliveries
4. **Configure Retry Settings** - Based on your Slack app reliability
5. **Use Descriptive User IDs** - For easier management
6. **Keep Configurations Simple** - Only override when necessary
7. **Test Different Interaction Types** - DMs, mentions, slash commands, and interactive buttons

## Security Considerations

- **Signature Verification**: All Slack requests are verified using the signing secret
- **Rate Limiting**: Built-in rate limiting prevents abuse
- **Bot Token Security**: Store bot tokens securely in Kubernetes secrets
- **Channel Access**: Bot only responds in channels it's invited to

## Related Documentation

- [Integration Guide](INTEGRATION_GUIDE.md) - Complete integration and request management guide
- [API Reference](API_REFERENCE.md) - API endpoints and usage
- [Authentication Guide](AUTHENTICATION_GUIDE.md) - Authentication setup