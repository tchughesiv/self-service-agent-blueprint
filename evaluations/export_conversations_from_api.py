#!/usr/bin/env python3
"""
Export real conversations from Request Manager into eval format.

Uses RequestManagerClient.get_conversations() (same as get-conversations-request-mgr.py)
to pull sessions from GET /api/v1/conversations, then saves each session as a JSON file
in the same format as generator.py and run_conversations.py:
- metadata.authoritative_user_id (from session user_email or user_id)
- metadata.description
- conversation: list of { "role": "user" | "assistant", "content": "..." }

Saved files go to results/conversation_results/ and can be evaluated with:
  uv run deep_eval.py --results-dir results/conversation_results

This script is invoked automatically by evaluate.py (step 2) with the same -n/--num-conversations
as generator.py, so params stay in sync when running the full pipeline.

Usage:
  uv run export_conversations_from_api.py -n 20
  REQUEST_MANAGER_URL=http://localhost:8080 uv run export_conversations_from_api.py -n 10
  uv run export_conversations_from_api.py -n 5 --user-email user@example.com
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime
from typing import Any

from shared_clients import RequestManagerClient


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export Request Manager conversations to eval-format JSON files"
    )
    parser.add_argument(
        "--request-manager-url",
        default=os.environ.get("REQUEST_MANAGER_URL", "http://localhost:8080"),
        help="Request Manager base URL",
    )
    parser.add_argument(
        "-n",
        "--num-conversations",
        type=int,
        default=None,
        help="Number of sessions to export (same as generator.py -n; default 20)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max sessions (overridden by -n/--num-conversations; default 20)",
    )
    parser.add_argument("--offset", type=int, default=0, help="Pagination offset")
    parser.add_argument("--user-email", help="Filter by user email")
    parser.add_argument("--user-id", help="Filter by user ID")
    parser.add_argument("--session-id", help="Filter by session ID")
    parser.add_argument(
        "--no-messages",
        action="store_true",
        help="Exclude conversation messages (only session metadata)",
    )
    parser.add_argument(
        "--output-dir",
        default="results/conversation_results",
        help="Output directory (default results/conversation_results)",
    )
    return parser.parse_args()


def _session_to_eval_format(session: dict[str, Any]) -> dict[str, Any]:
    """Convert one API session to eval format (metadata + conversation turns)."""
    authoritative_user_id = (
        session.get("user_email") or session.get("user_id") or "unknown"
    )
    sid = session.get("session_id", "unknown")[:8]
    metadata = {
        "authoritative_user_id": authoritative_user_id,
        "description": f"From Request Manager (session {sid})",
    }
    conversation: list[dict[str, str]] = []
    for item in session.get("conversation") or []:
        user_msg = item.get("user_message")
        agent_msg = item.get("agent_response")
        if user_msg is not None and str(user_msg).strip():
            conversation.append({"role": "user", "content": str(user_msg)})
        if agent_msg is not None and str(agent_msg).strip():
            conversation.append({"role": "assistant", "content": str(agent_msg)})
    return {"metadata": metadata, "conversation": conversation}


def _save_eval_file(payload: dict[str, Any], base_name: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{base_name}_{ts}.json"
    path = os.path.join(output_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    return path


async def _run(args: argparse.Namespace) -> int:
    # Same -n/--num-conversations as generator.py; --limit overrides if both set
    limit = args.limit if args.limit is not None else (args.num_conversations or 20)
    client = RequestManagerClient(request_manager_url=args.request_manager_url)
    try:
        data = await client.get_conversations(
            user_id=args.user_id,
            user_email=args.user_email,
            session_id=args.session_id,
            limit=limit,
            offset=args.offset,
            include_messages=not args.no_messages,
        )
    except Exception as e:
        print(f"Failed to fetch conversations: {e}", file=sys.stderr)
        return 1
    finally:
        await client.close()

    sessions = data.get("sessions") or []
    if not sessions:
        print("No sessions returned.", file=sys.stderr)
        return 0

    saved = []
    for session in sessions:
        eval_payload = _session_to_eval_format(session)
        if len(eval_payload["conversation"]) < 2:
            continue
        sid = (session.get("session_id") or "unknown")[:8]
        path = _save_eval_file(eval_payload, f"from_api_{sid}", args.output_dir)
        saved.append(path)
    print(f"Exported {len(saved)} conversation(s) to {args.output_dir}")
    for p in saved:
        print(f"  {p}")
    return 0


def main() -> int:
    args = _parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
