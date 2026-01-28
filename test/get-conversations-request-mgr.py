#!/usr/bin/env python3
"""
Retrieve conversations from Request Manager.

Uses the same auth and URL as chat-responses-request-mgr.py so it can be run
via pod exec or with REQUEST_MANAGER_URL. Output is JSON to stdout.
"""

import argparse
import asyncio
import json
import os
import sys

from shared_clients import RequestManagerClient

REQUEST_MANAGER_URL = os.environ.get("REQUEST_MANAGER_URL", "http://localhost:8080")
USER_ID = os.environ.get("USER_ID", None)
AUTHORITATIVE_USER_ID = os.environ.get("AUTHORITATIVE_USER_ID", None)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retrieve conversations from Request Manager (same auth as chat script)"
    )
    parser.add_argument(
        "--user-id",
        help="User ID for auth (default: env USER_ID or AUTHORITATIVE_USER_ID)",
    )
    parser.add_argument("--request-manager-url", help="Request Manager base URL")
    # All API filter params use --filter-* for consistency (auth uses --user-id)
    parser.add_argument(
        "--filter-user-id",
        help="Filter by user (UUID or email); API user_id",
    )
    parser.add_argument(
        "--filter-user-email",
        help="Filter by user email; API user_email (alternative to --filter-user-id)",
    )
    parser.add_argument("--filter-session-id", help="Filter by session ID")
    parser.add_argument(
        "--filter-start-date",
        help="Filter from date; ISO 8601 (e.g. 2026-01-01T00:00:00Z)",
    )
    parser.add_argument(
        "--filter-end-date",
        help="Filter to date; ISO 8601",
    )
    parser.add_argument(
        "--filter-integration-type",
        help="Filter by integration (CLI, WEB, SLACK, etc.)",
    )
    parser.add_argument("--filter-agent-id", help="Filter by agent ID")
    parser.add_argument(
        "--limit", type=int, default=100, help="Max results (default 100, max 1000)"
    )
    parser.add_argument("--offset", type=int, default=0, help="Pagination offset")
    parser.add_argument(
        "--no-messages", action="store_true", help="Exclude conversation messages"
    )
    parser.add_argument("--random", action="store_true", help="Random sampling")
    args = parser.parse_args()

    user_id = args.user_id or USER_ID or AUTHORITATIVE_USER_ID
    request_manager_url = args.request_manager_url or REQUEST_MANAGER_URL

    if not user_id:
        print(
            "Warning: No user ID (use --user-id or set USER_ID / AUTHORITATIVE_USER_ID)",
            file=sys.stderr,
        )

    client = RequestManagerClient(
        request_manager_url=request_manager_url,
        user_id=user_id or "retrieve-conversations",
    )

    try:
        result = await client.get_conversations(
            user_id=args.filter_user_id,
            user_email=args.filter_user_email,
            session_id=args.filter_session_id,
            start_date=args.filter_start_date,
            end_date=args.filter_end_date,
            integration_type=args.filter_integration_type,
            agent_id=args.filter_agent_id,
            limit=args.limit,
            offset=args.offset,
            include_messages=not args.no_messages,
            random=args.random,
        )
        print(json.dumps(result, indent=2, default=str))
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
