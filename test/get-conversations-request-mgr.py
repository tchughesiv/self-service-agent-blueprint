#!/usr/bin/env python3
"""
Retrieve conversations from Request Manager.

No auth required (matches generic). Run via pod exec or with REQUEST_MANAGER_URL.
Output is JSON to stdout.
"""

import argparse
import asyncio
import json
import os

from shared_clients import RequestManagerClient

REQUEST_MANAGER_URL = os.environ.get("REQUEST_MANAGER_URL", "http://localhost:8080")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Retrieve conversations from Request Manager (no auth required)"
    )
    parser.add_argument("--request-manager-url", help="Request Manager base URL")
    # API query params (same names as GET /api/v1/conversations)
    parser.add_argument("--user-id", help="Filter by user (UUID or email)")
    parser.add_argument("--user-email", help="Filter by user email")
    parser.add_argument("--session-id", help="Filter by session ID")
    parser.add_argument(
        "--start-date",
        help="Filter from date; ISO 8601 (e.g. 2026-01-01T00:00:00Z)",
    )
    parser.add_argument("--end-date", help="Filter to date; ISO 8601")
    parser.add_argument(
        "--integration-type",
        help="Filter by channel where the conversation started (session record)",
    )
    parser.add_argument(
        "--integration-types",
        nargs="*",
        metavar="TYPE",
        help="Only include sessions that used at least one of these channels (full conversation). E.g. --integration-types CLI SLACK",
    )
    parser.add_argument(
        "--agent-id",
        help="Filter by agent ID (sessions that used this agent, full conversation)",
    )
    parser.add_argument(
        "--limit", type=int, default=100, help="Max results (default 100, max 1000)"
    )
    parser.add_argument("--offset", type=int, default=0, help="Pagination offset")
    parser.add_argument(
        "--no-messages", action="store_true", help="Exclude conversation messages"
    )
    parser.add_argument("--random", action="store_true", help="Random sampling")
    args = parser.parse_args()
    request_manager_url = args.request_manager_url or REQUEST_MANAGER_URL

    client = RequestManagerClient(request_manager_url=request_manager_url)

    try:
        result = await client.get_conversations(
            user_id=args.user_id,
            user_email=args.user_email,
            session_id=args.session_id,
            start_date=args.start_date,
            end_date=args.end_date,
            integration_type=args.integration_type,
            integration_types=args.integration_types or None,
            agent_id=args.agent_id,
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
