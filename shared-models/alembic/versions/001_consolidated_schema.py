"""Consolidated database schema for self-service agent system.

Single migration creating the full schema. Use when starting fresh (pre-production).
Replaces migrations 001-004.

Note: request_id, cloudevent_id, and last_request_id use VARCHAR(255) to accommodate
email Message-IDs (e.g. <CAPbJ+...@mail.gmail.com>) which exceed UUID length (36).

Creates:
- Enums: integrationtype, sessionstatus, deliverystatus
- users, request_sessions (with version, partial unique for one active per user/integration)
- request_logs (with status, processing_started_at, indexes, updated_at trigger)
- user_integration_configs, integration_credentials, delivery_logs
- processed_events with UNIQUE(event_id, processed_by) for per-processor claims
- integration_default_configs
- user_integration_mappings (with partial unique for __NOT_FOUND__ sentinels)
- pod_heartbeats, event_outbox
- pg_advisory_lock_held function

Revision ID: 001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create full database schema."""
    connection = op.get_bind()

    # Create enums
    enums_to_create = [
        (
            "integrationtype",
            [
                "SLACK",
                "WEB",
                "CLI",
                "TOOL",
                "EMAIL",
                "SMS",
                "WEBHOOK",
                "TEAMS",
                "DISCORD",
                "TEST",
            ],
        ),
        ("sessionstatus", ["ACTIVE", "INACTIVE", "EXPIRED", "ARCHIVED"]),
        ("deliverystatus", ["PENDING", "DELIVERED", "FAILED", "RETRYING", "EXPIRED"]),
    ]
    for enum_name, enum_values in enums_to_create:
        values_str = "', '".join(enum_values)
        connection.execute(
            sa.text(
                f"""
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = '{enum_name}') THEN
                        CREATE TYPE {enum_name} AS ENUM ('{values_str}');
                    END IF;
                END $$;
                """
            )
        )

    integration_type_enum = postgresql.ENUM(name="integrationtype", create_type=False)
    session_status_enum = postgresql.ENUM(name="sessionstatus", create_type=False)
    delivery_status_enum = postgresql.ENUM(name="deliverystatus", create_type=False)

    # users
    op.create_table(
        "users",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("primary_email", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.create_index("ix_users_primary_email", "users", ["primary_email"], unique=True)

    # request_sessions (with version and partial unique from 003)
    op.create_table(
        "request_sessions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column("integration_type", integration_type_enum, nullable=False),
        sa.Column("status", session_status_enum, nullable=False),
        sa.Column("channel_id", sa.String(length=255), nullable=True),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("integration_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "total_requests",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "last_request_at", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("external_session_id", sa.String(length=255), nullable=True),
        sa.Column("current_agent_id", sa.String(length=255), nullable=True),
        sa.Column("conversation_thread_id", sa.String(length=255), nullable=True),
        sa.Column("user_context", sa.JSON(), nullable=True),
        sa.Column("conversation_context", sa.JSON(), nullable=True),
        sa.Column("last_request_id", sa.String(length=255), nullable=True),
        sa.Column(
            "total_input_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "total_output_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("llm_call_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "max_input_tokens_per_call",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "max_output_tokens_per_call",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "max_total_tokens_per_call",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_sessions")),
        sa.UniqueConstraint("session_id", name=op.f("uq_request_sessions_session_id")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_request_sessions_user_id"),
        ),
    )
    op.create_index(
        op.f("ix_session_id"), "request_sessions", ["session_id"], unique=False
    )
    op.create_index(
        "ix_request_sessions_user_id", "request_sessions", ["user_id"], unique=False
    )
    op.create_index(
        "ix_request_sessions_version",
        "request_sessions",
        ["version"],
        unique=False,
    )
    op.execute(
        """
        CREATE UNIQUE INDEX idx_one_active_session_per_user_integration
        ON request_sessions (user_id, integration_type)
        WHERE status = 'ACTIVE'
        """
    )

    # request_logs (with status, processing_started_at from 004)
    op.create_table(
        "request_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("request_type", sa.String(length=50), nullable=False),
        sa.Column("request_content", sa.Text(), nullable=False),
        sa.Column("normalized_request", sa.JSON(), nullable=True),
        sa.Column("agent_id", sa.String(length=255), nullable=True),
        sa.Column("processing_time_ms", sa.Integer(), nullable=True),
        sa.Column("response_content", sa.Text(), nullable=True),
        sa.Column("response_metadata", sa.JSON(), nullable=True),
        sa.Column("cloudevent_id", sa.String(length=255), nullable=True),
        sa.Column("cloudevent_type", sa.String(length=100), nullable=True),
        sa.Column("pod_name", sa.String(255), nullable=True),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="completed",
        ),
        sa.Column(
            "processing_started_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=True,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["request_sessions.session_id"],
            name=op.f("fk_request_logs_session_id_request_sessions"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_logs")),
        sa.UniqueConstraint("request_id", name=op.f("uq_request_logs_request_id")),
    )
    op.create_index(op.f("ix_request_id"), "request_logs", ["request_id"], unique=False)
    op.create_index(
        op.f("ix_request_logs_session_id"), "request_logs", ["session_id"], unique=False
    )
    op.create_index(
        op.f("ix_request_logs_agent_id"), "request_logs", ["agent_id"], unique=False
    )
    op.create_index(
        "ix_request_logs_pod_name", "request_logs", ["pod_name"], unique=False
    )
    op.create_index(
        "ix_request_logs_session_status_created",
        "request_logs",
        ["session_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_request_logs_status_processing_started",
        "request_logs",
        ["status", "processing_started_at"],
        postgresql_where=sa.text("status = 'processing'"),
    )

    # request_logs updated_at trigger
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION set_request_logs_updated_at()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = CURRENT_TIMESTAMP;
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql;
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER trg_request_logs_updated_at
            BEFORE UPDATE ON request_logs
            FOR EACH ROW EXECUTE PROCEDURE set_request_logs_updated_at();
            """
        )
    )

    # user_integration_configs
    op.create_table(
        "user_integration_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column("integration_type", integration_type_enum, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("retry_delay_seconds", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_integration_configs")),
        sa.UniqueConstraint("user_id", "integration_type", name="uq_user_integration"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_user_integration_configs_user_id"),
        ),
    )
    op.create_index(
        "ix_user_integration_configs_user_id",
        "user_integration_configs",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_integration_configs_integration_type",
        "user_integration_configs",
        ["integration_type"],
        unique=False,
    )

    # integration_credentials
    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_type", integration_type_enum, nullable=False),
        sa.Column("credential_name", sa.String(length=100), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_credentials")),
        sa.UniqueConstraint(
            "integration_type", "credential_name", name="uq_integration_credential"
        ),
    )

    # delivery_logs
    op.create_table(
        "delivery_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column("integration_config_id", sa.Integer(), nullable=True),
        sa.Column("integration_type", integration_type_enum, nullable=False),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", delivery_status_enum, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column(
            "first_attempt_at", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "last_attempt_at", postgresql.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column("delivered_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("expires_at", postgresql.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("error_details", sa.JSON(), nullable=True),
        sa.Column("integration_metadata", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(
            ["integration_config_id"],
            ["user_integration_configs.id"],
            name=op.f(
                "fk_delivery_logs_integration_config_id_user_integration_configs"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_delivery_logs_user_id"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_logs")),
    )
    op.create_index(
        "ix_delivery_logs_request_id", "delivery_logs", ["request_id"], unique=False
    )
    op.create_index(
        "ix_delivery_logs_session_id", "delivery_logs", ["session_id"], unique=False
    )
    op.create_index(
        "ix_delivery_logs_user_id", "delivery_logs", ["user_id"], unique=False
    )

    # processed_events - UNIQUE(event_id, processed_by) from the start (no legacy constraint)
    op.create_table(
        "processed_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("event_source", sa.String(length=255), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("session_id", sa.String(length=255), nullable=True),
        sa.Column("processed_by", sa.String(length=100), nullable=False),
        sa.Column("processing_result", sa.String(length=50), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_id",
            "processed_by",
            name="uq_processed_events_event_id_processed_by",
        ),
    )
    op.create_index(
        "ix_processed_events_event_id", "processed_events", ["event_id"], unique=False
    )
    op.create_index(
        "ix_processed_events_request_id",
        "processed_events",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        "ix_processed_events_created_at",
        "processed_events",
        ["created_at"],
        unique=False,
    )

    # integration_default_configs
    op.create_table(
        "integration_default_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("integration_type", integration_type_enum, nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("retry_delay_seconds", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_default_configs")),
        sa.UniqueConstraint("integration_type", name="uq_integration_default_type"),
    )

    # user_integration_mappings (with partial unique from 002)
    op.create_table(
        "user_integration_mappings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_email", sa.String(length=255), nullable=False),
        sa.Column(
            "integration_type",
            postgresql.ENUM(name="integrationtype", create_type=False),
            nullable=False,
        ),
        sa.Column("integration_user_id", sa.String(length=255), nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            nullable=False,
        ),
        sa.Column("last_validated_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "validation_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_validation_error", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True, default="system"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.user_id"],
            name=op.f("fk_user_integration_mappings_user_id"),
        ),
    )
    op.create_index(
        "ix_user_integration_mapping_email_type",
        "user_integration_mappings",
        ["user_email", "integration_type"],
    )
    op.create_index(
        "ix_user_integration_mappings_user_email",
        "user_integration_mappings",
        ["user_email"],
    )
    op.create_index(
        "ix_user_integration_mapping_user_id",
        "user_integration_mappings",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_user_integration_mapping_user_type",
        "user_integration_mappings",
        ["user_id", "integration_type"],
        unique=False,
    )
    op.create_index(
        "ix_user_integration_mapping_integration",
        "user_integration_mappings",
        ["integration_user_id", "integration_type"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_user_integration_mapping",
        "user_integration_mappings",
        ["user_id", "integration_type"],
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_integration_user_id_type
        ON user_integration_mappings (integration_user_id, integration_type)
        WHERE integration_user_id != '__NOT_FOUND__';
        """
    )

    # pod_heartbeats (from 004)
    op.create_table(
        "pod_heartbeats",
        sa.Column("pod_name", sa.String(length=255), nullable=False),
        sa.Column(
            "last_check_in_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("pod_name", name=op.f("pk_pod_heartbeats")),
    )

    # event_outbox (from 004)
    op.create_table(
        "event_outbox",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("source_service", sa.String(length=255), nullable=False),
        sa.Column("event_type", sa.String(length=255), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column(
            "thread_order_key",
            sa.String(length=512),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status", sa.String(length=50), nullable=False, server_default="pending"
        ),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_event_outbox")),
    )
    op.create_index(
        "ix_event_outbox_status_created",
        "event_outbox",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_event_outbox_thread_order_created",
        "event_outbox",
        ["thread_order_key", "created_at"],
        unique=False,
    )
    op.create_unique_constraint(
        "uq_event_outbox_source_type_idempotency",
        "event_outbox",
        ["source_service", "event_type", "idempotency_key"],
    )

    # pg_advisory_lock_held
    op.execute(
        """
        CREATE OR REPLACE FUNCTION pg_advisory_lock_held(key BIGINT)
        RETURNS BOOLEAN
        LANGUAGE plpgsql
        AS $$
        DECLARE
            lock_count INTEGER;
        BEGIN
            SELECT COUNT(*)
            INTO lock_count
            FROM pg_locks
            WHERE locktype = 'advisory'
              AND objid = key::BIGINT
              AND pid = pg_backend_pid()
              AND granted = TRUE;
            RETURN lock_count > 0;
        END;
        $$;
        """
    )


def downgrade() -> None:
    """Drop all schema objects."""
    op.execute("DROP FUNCTION IF EXISTS pg_advisory_lock_held(BIGINT);")

    op.drop_constraint(
        "uq_event_outbox_source_type_idempotency",
        "event_outbox",
        type_="unique",
    )
    op.drop_index("ix_event_outbox_thread_order_created", table_name="event_outbox")
    op.drop_index("ix_event_outbox_status_created", table_name="event_outbox")
    op.drop_table("event_outbox")

    op.drop_table("pod_heartbeats")

    op.drop_index("uq_integration_user_id_type", "user_integration_mappings")
    op.drop_constraint(
        "uq_user_integration_mapping",
        "user_integration_mappings",
        type_="unique",
    )
    op.drop_table("user_integration_mappings")

    op.drop_table("integration_default_configs")
    op.drop_table("processed_events")
    op.drop_table("delivery_logs")
    op.drop_table("integration_credentials")
    op.drop_table("user_integration_configs")

    op.execute(
        sa.text("DROP TRIGGER IF EXISTS trg_request_logs_updated_at ON request_logs")
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS set_request_logs_updated_at()"))

    op.drop_index(
        "ix_request_logs_status_processing_started",
        table_name="request_logs",
    )
    op.drop_index(
        "ix_request_logs_session_status_created",
        table_name="request_logs",
    )
    op.drop_table("request_logs")

    op.execute("DROP INDEX IF EXISTS idx_one_active_session_per_user_integration")
    op.drop_table("request_sessions")
    op.drop_table("users")

    for enum_name in ["deliverystatus", "sessionstatus", "integrationtype"]:
        connection = op.get_bind()
        result = connection.execute(
            sa.text(f"SELECT 1 FROM pg_type WHERE typname = '{enum_name}'")
        )
        if result.fetchone():
            connection.execute(sa.text(f"DROP TYPE {enum_name}"))
