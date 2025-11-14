# Canonical User ID Architecture

## Problem

The system previously used email addresses (or Slack user IDs as fallback) as `user_id` across all tables, causing several issues:

1. **Inconsistent user_id resolution**: When Slack couldn't fetch email, it fell back to Slack user ID (e.g., "U09EPAXGNLS"), while email used email address (e.g., "user@example.com")
2. **Session continuity breaks**: Sessions created with different user_ids couldn't be matched across integrations
3. **No single source of truth**: User identity was fragmented across integration mappings
4. **Database type mismatches**: Email addresses and Slack IDs are strings, but the database expects UUIDs

## Solution

Introduced a canonical `User` table with a UUID-based `user_id` that all integrations reference. This ensures consistent user identity across all integrations and enables seamless session continuity.

## Key Changes

### Database Schema

- **New `users` table**: Contains canonical user identity with UUID `user_id` and optional `primary_email`
- **Updated tables**: `RequestSession`, `UserIntegrationMapping`, `UserIntegrationConfig`, and `DeliveryLog` now reference `users.user_id` via foreign keys
- **Unique constraints**:
  - `User.primary_email` - prevents duplicate users with same email
  - `(user_id, integration_type)` - one mapping per user per integration type
  - `(integration_user_id, integration_type)` - prevents same Slack user ID from mapping to different canonical users

### User Resolution

- **`get_or_create_canonical_user()`**: Creates or retrieves canonical user by email with race condition handling
- **`resolve_user_id_from_email()`**: Returns canonical user_id (UUID) instead of email address
- **`resolve_user_id_from_integration_id()`**: New function to resolve canonical user_id from Slack user ID when email is unavailable
- **`store_user_mapping()`**: Updated to create/get canonical users and return canonical user_id

### Service Updates

- **Slack Service**: 
  - Resolves to canonical user_id even when using cached email
  - Handles case when email is unavailable by checking existing mappings
  - Raises error if no mapping exists (prevents invalid UUID errors)
  
- **Email Service**: 
  - Updated to use canonical user_id resolution
  - Removed fallback that returned email address (would cause database errors)

### Migration

- **Migration `007_add_canonical_user_id.py`**:
  - Creates `users` table
  - Migrates existing data from `user_integration_mappings` to create canonical users
  - Updates all related tables to use canonical user_id
  - Handles data migration for existing sessions, configs, and delivery logs
  - Uses raw SQL for column renames (compatibility with Alembic versions)

### Race Condition Handling

- **Try-create, catch-duplicate pattern**: `get_or_create_canonical_user()` handles concurrent requests by catching unique constraint violations and retrying lookup
- **Upsert patterns**: `store_user_mapping()` uses upsert to handle conflicts gracefully

## Benefits

1. **Consistent identity**: All integrations reference the same canonical user_id
2. **Session continuity**: Sessions work across all integrations automatically
3. **Flexible resolution**: Can resolve canonical user_id from any integration identifier
4. **Future-proof**: Easy to add new integrations without identity conflicts
5. **Better data integrity**: Foreign key constraints ensure referential integrity
6. **Prevents conflicts**: Unique constraints prevent duplicate email assignments and mapping conflicts

## Breaking Changes

⚠️ **Database migration required**: This change requires running migration `007_add_canonical_user_id.py`. The migration:
- Creates new `users` table
- Migrates existing data
- Updates all foreign key relationships
- Should be run during a maintenance window

## Testing Considerations

- Verify existing users are migrated correctly
- Test session continuity across Slack and Email integrations
- Test race conditions with concurrent user creation
- Verify error handling when email is unavailable for Slack users
- Test that unique constraints prevent duplicate assignments

## Files Changed

### Core Models & Migration
- `shared-models/src/shared_models/models.py` - Added User model, updated foreign keys
- `shared-models/alembic/versions/007_add_canonical_user_id.py` - Migration script

### User Resolution
- `integration-dispatcher/src/integration_dispatcher/user_mapping_utils.py` - Core resolution functions
- `integration-dispatcher/src/integration_dispatcher/slack_service.py` - Slack user resolution
- `integration-dispatcher/src/integration_dispatcher/email_service.py` - Email user resolution

