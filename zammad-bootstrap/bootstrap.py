#!/usr/bin/env python3
"""Zammad bootstrap: creates group, custom user attribute, and all users.

Authenticates with admin basic auth to self-issue a temporary API token,
so this script has no dependency on an externally provisioned token.

Required env vars:
  ZAMMAD_BASE_URL       e.g. http://zammad-nginx:8080
  ZAMMAD_ADMIN_EMAIL    admin user created by autoWizard
  ZAMMAD_ADMIN_PASSWORD matching password

Optional (integration webhook — same secret as integration-dispatcher ZAMMAD_WEBHOOK_SECRET):
  ZAMMAD_INTEGRATION_WEBHOOK_URL   Full URL e.g. http://RELEASE-integration-dispatcher.NS.svc.cluster.local/zammad/webhook
  ZAMMAD_WEBHOOK_SECRET            HMAC token stored on Zammad Webhook + verified by dispatcher (omit = unsigned webhook)
  ZAMMAD_CUSTOMER_SENDER_ID       Defaults to 2 (standard Zammad seed); override if your DB differs

Optional trigger narrowing (Zammad trigger conditions — preferred over dispatcher env allowlists):
  ZAMMAD_TRIGGER_GROUP_IDS   Comma-separated internal group IDs (e.g. "3" or "3,5"). Adds ticket.group_id (is / is any of). Overrides GROUP_NAMES if both set.
  ZAMMAD_TRIGGER_GROUP_NAMES  Comma-separated Zammad group names (e.g. "Users" or "Users,human_managed_tickets"). Resolved to ids via GET /api/v1/groups at trigger update time. Helm: bootstrap.integrationWebhook.triggerGroupNames.
  ZAMMAD_TRIGGER_SKIP_GROUP_FILTER  If true/1/yes: do not add ticket.group_id (fire for customer articles in any group).
  ZAMMAD_TRIGGER_TAGS_ANY    Comma-separated tag names; ticket must contain at least one ("contains one"). Mutually exclusive with TAGS_ALL.
  ZAMMAD_TRIGGER_TAGS_ALL    Comma-separated tag names; ticket must contain all ("contains all"). Mutually exclusive with TAGS_ANY.
"""

import base64
import datetime
import json
import os
import sys
import time

import requests
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config
from mock_employee_data.data import MOCK_EMPLOYEE_DATA

BASE_URL = os.environ["ZAMMAD_BASE_URL"].rstrip("/")
API_URL = f"{BASE_URL}/api/v1"
ADMIN_EMAIL = os.environ["ZAMMAD_ADMIN_EMAIL"]
ADMIN_PASSWORD = os.environ["ZAMMAD_ADMIN_PASSWORD"]
AUTOWIZARD_TOKEN = os.environ["ZAMMAD_AUTOWIZARD_TOKEN"]

# Webhook + trigger (Manage → Webhooks + Triggers); see docs/TICKETING_CHANNEL_GAMEPLAN.md §5.2.1
WEBHOOK_RECORD_NAME = "Self-Service Agent — Integration Webhook"
TRIGGER_RECORD_NAME = "Self-Service Agent — Customer article → blueprint"

MANAGER1_EMAIL = "manager1@example.com"
MANAGER2_EMAIL = "manager2@example.com"
DEFAULT_PASSWORD = "ChangeMe123!"

SESSION = requests.Session()
SESSION.headers.update({"Content-Type": "application/json"})


# ---------------------------------------------------------------------------
# Startup: wait for Zammad, then self-issue an API token
# ---------------------------------------------------------------------------


def wait_for_zammad(timeout=600, interval=10):
    print("Waiting for Zammad to be healthy...")
    health_url = f"{API_URL}/getting_started"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if requests.get(health_url, timeout=5).ok:
                print("Zammad is healthy.")
                return
        except requests.exceptions.RequestException:
            pass
        print(f"  Not ready yet, retrying in {interval}s...")
        time.sleep(interval)
    print("ERROR: Zammad did not become healthy in time.", file=sys.stderr)
    sys.exit(1)


def trigger_autowizard(timeout=120, interval=10):
    """Trigger the autoWizard via HTTP to seed the initial admin user."""
    print("Triggering autoWizard...")
    url = f"{API_URL}/getting_started/auto_wizard/{AUTOWIZARD_TOKEN}"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(url, timeout=10)
            if r.ok:
                print("  autoWizard triggered successfully.")
                return
            print(f"  autoWizard returned {r.status_code}, retrying in {interval}s...")
        except requests.exceptions.RequestException as e:
            print(f"  Request error: {e}, retrying in {interval}s...")
        time.sleep(interval)
    print("ERROR: Could not trigger autoWizard.", file=sys.stderr)
    sys.exit(1)


def acquire_token(timeout=120, interval=10):
    """Create a short-lived API token using admin basic auth."""
    print("Acquiring API token via admin credentials...")
    url = f"{API_URL}/user_access_token"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.post(
                url,
                auth=(ADMIN_EMAIL, ADMIN_PASSWORD),
                json={"name": "zammad-bootstrap", "permission": ["admin"]},
                timeout=10,
            )
            if r.ok:
                token = r.json().get("token")
                if token:
                    SESSION.headers["Authorization"] = f"Token token={token}"
                    print("  API token acquired.")
                    return
            print(
                f"  Token creation returned {r.status_code}: {r.text[:200]}, retrying in {interval}s..."
            )
        except requests.exceptions.RequestException as e:
            print(f"  Request error: {e}, retrying in {interval}s...")
        time.sleep(interval)
    print("ERROR: Could not acquire API token.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def api(method, path, **kwargs):
    url = f"{API_URL}/{path.lstrip('/')}"
    resp = SESSION.request(method, url, **kwargs)
    if not resp.ok:
        print(
            f"  ERROR {method} {path}: {resp.status_code} {resp.text[:300]}",
            file=sys.stderr,
        )
        resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Group
# ---------------------------------------------------------------------------


def get_or_create_group(name):
    for g in api("GET", "groups"):
        if g["name"] == name:
            print(f"  Group '{name}' already exists (id={g['id']})")
            return g["id"]
    g = api("POST", "groups", json={"name": name, "active": True})
    print(f"  Created group '{name}' (id={g['id']})")
    return g["id"]


def lookup_group_id_by_name(name: str) -> int | None:
    """Return Zammad internal group id for an exact group name, or None."""
    for g in api("GET", "groups"):
        if g.get("name") == name:
            return int(g["id"])
    return None


# ---------------------------------------------------------------------------
# Custom attribute
# ---------------------------------------------------------------------------


def build_current_laptop_json(emp: dict) -> str:
    """Build the JSON string to store in the current_laptop Zammad field."""
    purchase_date = emp.get("purchase_date", "N/A")
    warranty_expiry = emp.get("warranty_expiry", "N/A")
    location = (emp.get("location") or "N/A").upper()

    return json.dumps(
        {
            "name": emp.get("name", "N/A"),
            "location": location,
            "laptop_model": emp.get("laptop_model", "N/A"),
            "serial_number": emp.get("laptop_serial_number", "N/A"),
            "purchase_date": purchase_date,
            "warranty_expiry": warranty_expiry,
        }
    )


def ensure_manager_email_attribute():
    for a in api("GET", "object_manager_attributes"):
        if a.get("object") == "User" and a.get("name") == "manager_email":
            print("  Custom attribute 'manager_email' already exists.")
            return

    print("  Creating custom attribute 'manager_email' on User...")
    api(
        "POST",
        "object_manager_attributes",
        json={
            "object": "User",
            "name": "manager_email",
            "display": "Manager Email",
            "data_type": "input",
            "data_option": {
                "type": "email",
                "maxlength": 255,
                "null": True,
                "note": "",
            },
            "active": True,
            "screens": {
                "edit": {
                    "Admin": {"shown": True, "required": False},
                    "Agent": {"shown": True, "required": False},
                },
                "view": {
                    "Admin": {"shown": True, "required": False},
                    "Agent": {"shown": True, "required": False},
                },
            },
        },
    )
    print("  Executing attribute migrations...")
    api("POST", "object_manager_attributes_execute_migrations")
    print("  Attribute migration complete.")


def ensure_current_laptop_attribute():
    for a in api("GET", "object_manager_attributes"):
        if a.get("object") == "User" and a.get("name") == "current_laptop":
            print("  Custom attribute 'current_laptop' already exists.")
            return

    print("  Creating custom attribute 'current_laptop' on User...")
    api(
        "POST",
        "object_manager_attributes",
        json={
            "object": "User",
            "name": "current_laptop",
            "display": "Current Laptop",
            "data_type": "textarea",
            "data_option": {
                "maxlength": 5000,
                "null": True,
                "note": "",
            },
            "active": True,
            "screens": {
                "edit": {
                    "Admin": {"shown": True, "required": False},
                    "Agent": {"shown": True, "required": False},
                },
                "view": {
                    "Admin": {"shown": True, "required": False},
                    "Agent": {"shown": True, "required": False},
                },
            },
        },
    )
    print("  Executing attribute migrations...")
    api("POST", "object_manager_attributes_execute_migrations")
    print("  Attribute migration complete.")


# ---------------------------------------------------------------------------
# Roles
# ---------------------------------------------------------------------------


def get_role_ids():
    roles = api("GET", "roles")
    return {r["name"]: r["id"] for r in roles}


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------


def find_user_by_email(email):
    """Look up a user by email using paginated listing (avoids Elasticsearch lag on fresh deploys)."""
    page = 1
    while True:
        users = api("GET", f"users?page={page}&per_page=100")
        if not users:
            return None
        for u in users:
            if u.get("email", "").lower() == email.lower():
                return u
        if len(users) < 100:
            return None
        page += 1


def get_or_create_user(
    login,
    firstname,
    lastname,
    email,
    role_ids,
    group_ids=None,
    manager_email=None,
    current_laptop=None,
):
    existing = find_user_by_email(email)
    payload = {
        "login": login,
        "firstname": firstname,
        "lastname": lastname,
        "email": email,
        "role_ids": role_ids,
        "password": DEFAULT_PASSWORD,
        "active": True,
    }
    if group_ids:
        payload["group_ids"] = group_ids
    if manager_email:
        payload["manager_email"] = manager_email
    if current_laptop:
        payload["current_laptop"] = current_laptop

    if existing:
        print(f"  User '{email}' already exists (id={existing['id']}), updating...")
        api("PUT", f"users/{existing['id']}", json=payload)
    else:
        user = api("POST", "users", json=payload)
        print(f"  Created user '{email}' (id={user['id']})")


# ---------------------------------------------------------------------------
# Token creation + Kubernetes secret/deployment update
# ---------------------------------------------------------------------------

_KUBE_NS = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


def create_mcp_token_and_update_k8s():
    """Create Zammad MCP agent API token, update k8s secret (read+replace), restart MCP + dispatcher."""
    credentials_secret = os.environ["ZAMMAD_CREDENTIALS_SECRET"]
    mcp_deployment = os.environ.get("ZAMMAD_MCP_DEPLOYMENT", "")
    integration_dispatcher_deployment = os.environ.get(
        "ZAMMAD_INTEGRATION_DISPATCHER_DEPLOYMENT", ""
    )
    zammad_url = os.environ["ZAMMAD_BASE_URL"]

    with open(_KUBE_NS) as f:
        namespace = f.read().strip()

    print("\n[Token] Creating MCP agent API token...")
    r = requests.post(
        f"{API_URL}/user_access_token",
        auth=(ADMIN_EMAIL, ADMIN_PASSWORD),
        json={"name": "mcp-agent", "permission": ["admin", "ticket.agent"]},
        timeout=10,
    )
    if not r.ok:
        print(f"  ERROR: {r.status_code} {r.text[:200]}", file=sys.stderr)
        sys.exit(1)
    token = r.json().get("token")
    if not token:
        print("  ERROR: no token in response", file=sys.stderr)
        sys.exit(1)
    print("  MCP token created.")

    k8s_config.load_incluster_config()
    core_v1 = k8s_client.CoreV1Api()
    apps_v1 = k8s_client.AppsV1Api()

    def _b64(s):
        return base64.b64encode(s.encode()).decode()

    print(f"  Updating secret {credentials_secret} in namespace {namespace}...")
    try:
        # Use read + replace instead of PATCH: some clusters (notably OpenShift) return 401 or other
        # failures on strategic-merge PATCH for Secrets even when RBAC allows patch/update.
        existing = core_v1.read_namespaced_secret(
            name=credentials_secret, namespace=namespace
        )
        data = dict(existing.data or {})
        data["zammad-url"] = _b64(zammad_url)
        data["zammad-api-url"] = _b64(f"{zammad_url}/api/v1")
        data["zammad-http-token"] = _b64(token)
        existing.data = data
        core_v1.replace_namespaced_secret(
            name=credentials_secret,
            namespace=namespace,
            body=existing,
        )
    except k8s_client.ApiException as e:
        detail = (e.body or "").strip()[:500]
        extra = f" — {detail}" if detail else ""
        print(
            f"  ERROR updating secret: {e.status} {e.reason}{extra}",
            file=sys.stderr,
        )
        sys.exit(1)
    print("  Secret updated.")

    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    restart_patch = {
        "spec": {
            "template": {
                "metadata": {"annotations": {"kubectl.kubernetes.io/restartedAt": now}}
            }
        }
    }
    for dep in filter(
        None,
        [mcp_deployment, integration_dispatcher_deployment],
    ):
        print(f"  Restarting deployment {dep}...")
        try:
            apps_v1.patch_namespaced_deployment(
                name=dep,
                namespace=namespace,
                body=restart_patch,
            )
            print(f"  Deployment {dep} restart triggered.")
        except k8s_client.ApiException as e:
            print(
                f"  WARNING: could not restart {dep}: {e.status}",
                file=sys.stderr,
            )
    print("[Token] Done.")


# ---------------------------------------------------------------------------
# Integration webhook + trigger (Zammad → POST /zammad/webhook)
# ---------------------------------------------------------------------------


def _ssl_verify_for_endpoint(endpoint: str) -> bool:
    return endpoint.strip().lower().startswith("https://")


def _customer_sender_id() -> int:
    raw = os.environ.get("ZAMMAD_CUSTOMER_SENDER_ID", "").strip()
    if raw.isdigit():
        return int(raw)
    return 2


def _get_record_list(path: str):
    """GET /api/v1/{path}; return list of records."""
    data = api("GET", path)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        rec = data.get("records")
        if isinstance(rec, list):
            return rec
    return []


def _find_by_name(records, name: str):
    for r in records:
        if r.get("name") == name:
            return r
    return None


def _split_csv_env(key: str) -> list[str]:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _env_truthy(key: str) -> bool:
    return os.environ.get(key, "").strip().lower() in ("1", "true", "yes", "on")


def _integration_trigger_condition(sender_id: int) -> tuple[dict, list[str]]:
    """Build Zammad trigger ``condition`` dict (article create + customer sender + optional filters).

    Returns (condition, log_notes).
    """
    condition = {
        "article.action": {"operator": "is", "value": "create"},
        "article.sender_id": {"operator": "is", "value": sender_id},
    }
    notes: list[str] = []

    skip_group = _env_truthy("ZAMMAD_TRIGGER_SKIP_GROUP_FILTER")
    raw_groups = os.environ.get("ZAMMAD_TRIGGER_GROUP_IDS", "").strip()
    raw_names = os.environ.get("ZAMMAD_TRIGGER_GROUP_NAMES", "").strip()

    if skip_group:
        notes.append("group filter off (ZAMMAD_TRIGGER_SKIP_GROUP_FILTER)")
        if raw_groups or raw_names:
            notes.append(
                "(ZAMMAD_TRIGGER_SKIP_GROUP_FILTER ignores GROUP_IDS / GROUP_NAMES)"
            )
    elif raw_groups:
        ids: list[int] = []
        for part in raw_groups.split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        if ids:
            if len(ids) == 1:
                condition["ticket.group_id"] = {"operator": "is", "value": ids[0]}
            else:
                condition["ticket.group_id"] = {"operator": "is any of", "value": ids}
            notes.append(
                f"ticket.group_id from ZAMMAD_TRIGGER_GROUP_IDS ({len(ids)} id(s))"
            )
    elif raw_names:
        names = [x.strip() for x in raw_names.split(",") if x.strip()]
        ids_from_names: list[int] = []
        for n in names:
            gid = lookup_group_id_by_name(n)
            if gid is None:
                print(
                    f"ERROR: ZAMMAD_TRIGGER_GROUP_NAMES: no Zammad group named {n!r}. "
                    "Create the group or fix the spelling.",
                    file=sys.stderr,
                )
                sys.exit(1)
            ids_from_names.append(gid)
        if len(ids_from_names) == 1:
            condition["ticket.group_id"] = {
                "operator": "is",
                "value": ids_from_names[0],
            }
        else:
            condition["ticket.group_id"] = {
                "operator": "is any of",
                "value": ids_from_names,
            }
        notes.append(
            f"ticket.group_id from ZAMMAD_TRIGGER_GROUP_NAMES {names} -> ids {ids_from_names}"
        )

    tags_any = _split_csv_env("ZAMMAD_TRIGGER_TAGS_ANY")
    tags_all = _split_csv_env("ZAMMAD_TRIGGER_TAGS_ALL")
    if tags_any and tags_all:
        print(
            "ERROR: Set only one of ZAMMAD_TRIGGER_TAGS_ANY or ZAMMAD_TRIGGER_TAGS_ALL "
            "(two ticket.tags rules require Zammad expert mode — configure manually).",
            file=sys.stderr,
        )
        sys.exit(1)
    if tags_any:
        val: list[str] | str = tags_any[0] if len(tags_any) == 1 else tags_any
        condition["ticket.tags"] = {"operator": "contains one", "value": val}
    elif tags_all:
        val = tags_all[0] if len(tags_all) == 1 else tags_all
        condition["ticket.tags"] = {"operator": "contains all", "value": val}

    return condition, notes


def ensure_integration_webhook_and_trigger():
    """Create or update Webhook + Trigger per TICKETING_CHANNEL_GAMEPLAN §5.2.1."""
    endpoint = os.environ.get("ZAMMAD_INTEGRATION_WEBHOOK_URL", "").strip()
    if not endpoint:
        print(
            "\n[6/6] Skipping Zammad→blueprint webhook bootstrap "
            "(unset ZAMMAD_INTEGRATION_WEBHOOK_URL — configure manually per docs §5.2)."
        )
        return

    secret = os.environ.get("ZAMMAD_WEBHOOK_SECRET", "").strip()
    sender_id = _customer_sender_id()

    print(
        "\n[6/6] Ensuring Zammad Webhook + Trigger (customer articles → integration-dispatcher)..."
    )
    webhooks = _get_record_list("webhooks")
    hook = _find_by_name(webhooks, WEBHOOK_RECORD_NAME)

    webhook_body = {
        "name": WEBHOOK_RECORD_NAME,
        "endpoint": endpoint,
        "http_method": "post",
        "ssl_verify": _ssl_verify_for_endpoint(endpoint),
        "active": True,
    }
    if secret:
        webhook_body["signature_token"] = secret

    if hook:
        webhook_id = hook["id"]
        print(f"  Updating webhook '{WEBHOOK_RECORD_NAME}' (id={webhook_id})...")
        api("PUT", f"webhooks/{webhook_id}", json=webhook_body)
    else:
        print(f"  Creating webhook '{WEBHOOK_RECORD_NAME}'...")
        created = api("POST", "webhooks", json=webhook_body)
        webhook_id = created["id"]

    triggers = _get_record_list("triggers")
    trig = _find_by_name(triggers, TRIGGER_RECORD_NAME)

    trigger_condition, cond_notes = _integration_trigger_condition(sender_id)
    for line in cond_notes:
        print(f"  {line}")
    extra = []
    if "ticket.group_id" in trigger_condition:
        extra.append("ticket.group_id")
    if "ticket.tags" in trigger_condition:
        extra.append("ticket.tags")
    if extra:
        print(
            f"  Trigger conditions include: article create + customer sender + {', '.join(extra)}"
        )

    trigger_body = {
        "name": TRIGGER_RECORD_NAME,
        "activator": "action",
        "execution_condition_mode": "selective",
        "condition": trigger_condition,
        "perform": {"notification.webhook": {"webhook_id": webhook_id}},
        "active": True,
    }

    if trig:
        tid = trig["id"]
        print(f"  Updating trigger '{TRIGGER_RECORD_NAME}' (id={tid})...")
        api("PUT", f"triggers/{tid}", json=trigger_body)
    else:
        print(f"  Creating trigger '{TRIGGER_RECORD_NAME}'...")
        api("POST", "triggers", json=trigger_body)

    print("  Done — Zammad will POST customer articles to the configured URL.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    wait_for_zammad()
    trigger_autowizard()
    acquire_token()

    print("\n[1/6] Ensuring custom attributes exist...")
    ensure_manager_email_attribute()
    ensure_current_laptop_attribute()

    print("\n[2/6] Creating groups...")
    users_group_id = get_or_create_group("Users")
    group_id = get_or_create_group("human_managed_tickets")
    escalated_group_id = get_or_create_group("escalated_laptop_refresh_tickets")

    print("\n[3/6] Looking up role IDs...")
    role_map = get_role_ids()
    agent_role_ids = [role_map["Agent"]]
    customer_role_ids = [role_map["Customer"]]
    print(f"  Agent role id={agent_role_ids}, Customer role id={customer_role_ids}")

    print("\n[3b/6] Adding admin user to all groups...")
    admin = find_user_by_email("admin@zammad.local")
    if admin:
        api(
            "PUT",
            f"users/{admin['id']}",
            json={
                "group_ids": {
                    str(users_group_id): ["full"],
                    str(group_id): ["full"],
                    str(escalated_group_id): ["full"],
                }
            },
        )
        print(f"  Admin user (id={admin['id']}) added to all groups.")
    else:
        print("  WARNING: admin@zammad.local not found, skipping group assignment.")

    print("\n[4/6] Creating ticket handlers, managers and specialists...")
    get_or_create_user(
        "agent.laptop-specialist",
        "Laptop",
        "Specialist",
        "agent.laptop-specialist@example.com",
        role_ids=agent_role_ids,
        group_ids={str(users_group_id): ["full"]},
    )
    get_or_create_user(
        "agent.general",
        "General",
        "Agent",
        "agent.general@example.com",
        role_ids=agent_role_ids,
        group_ids={str(users_group_id): ["full"]},
    )
    get_or_create_user(
        "ticket_handler1",
        "Ticket",
        "Handler1",
        "ticket_handler1@example.com",
        role_ids=agent_role_ids,
        group_ids={str(group_id): ["full"]},
    )
    get_or_create_user(
        "ticket_handler2",
        "Ticket",
        "Handler2",
        "ticket_handler2@example.com",
        role_ids=agent_role_ids,
        group_ids={str(group_id): ["full"]},
    )
    get_or_create_user(
        "escalated_laptop_refresh_handler1",
        "Escalated Laptop Refresh",
        "Handler1",
        "escalated_laptop_refresh_handler1@example.com",
        role_ids=agent_role_ids,
        group_ids={str(escalated_group_id): ["full"]},
    )
    get_or_create_user(
        "escalated_laptop_refresh_handler2",
        "Escalated Laptop Refresh",
        "Handler2",
        "escalated_laptop_refresh_handler2@example.com",
        role_ids=agent_role_ids,
        group_ids={str(escalated_group_id): ["full"]},
    )
    get_or_create_user(
        "manager1",
        "Manager",
        "One",
        MANAGER1_EMAIL,
        role_ids=agent_role_ids + customer_role_ids,
        group_ids={str(users_group_id): ["full"]},
    )
    get_or_create_user(
        "manager2",
        "Manager",
        "Two",
        MANAGER2_EMAIL,
        role_ids=agent_role_ids + customer_role_ids,
        group_ids={str(users_group_id): ["full"]},
    )

    print("\n[5/6] Creating employees from mock-employee-data...")
    employees = list(MOCK_EMPLOYEE_DATA.values())
    for i, emp in enumerate(employees):
        manager_email = MANAGER1_EMAIL if i < 5 else MANAGER2_EMAIL
        name_parts = emp["name"].split(maxsplit=1)
        get_or_create_user(
            emp["user_name"],
            name_parts[0],
            name_parts[1] if len(name_parts) > 1 else "",
            emp["email"],
            role_ids=customer_role_ids,
            manager_email=manager_email,
            current_laptop=build_current_laptop_json(emp),
        )

    ensure_integration_webhook_and_trigger()

    print("\nZammad bootstrap complete.")

    if os.environ.get("ZAMMAD_CREATE_TOKEN") == "true":
        create_mcp_token_and_update_k8s()


if __name__ == "__main__":
    main()
