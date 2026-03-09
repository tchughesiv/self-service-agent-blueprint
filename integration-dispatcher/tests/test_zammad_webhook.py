"""Unit tests for Zammad webhook signature and helpers (APPENG-4759)."""

import hashlib
import hmac

from integration_dispatcher.zammad_service import (
    _customer_email_from_ticket,
    _group_name_from_ticket,
    _strip_html_body,
    build_zammad_ticket_thread_key,
    canonical_ticket_customer_email,
    verify_zammad_webhook_signature,
)


def test_build_zammad_ticket_thread_key() -> None:
    assert build_zammad_ticket_thread_key(42) == "integration:zammad:ticket:42"


def test_verify_zammad_webhook_signature_hmac() -> None:
    body = b'{"hello":"world"}'
    secret = "mytriggersecret"
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    assert verify_zammad_webhook_signature(body, f"sha1={expected}", secret) is True
    assert verify_zammad_webhook_signature(body, expected, secret) is True
    assert verify_zammad_webhook_signature(body, "sha1=deadbeef", secret) is False
    assert verify_zammad_webhook_signature(body, None, secret) is False


def test_verify_zammad_webhook_signature_empty_secret() -> None:
    body = b"{}"
    assert verify_zammad_webhook_signature(body, None, "") is True


def test_strip_html_body() -> None:
    assert _strip_html_body("<p>Hello <b>world</b></p>") == "Hello world"
    assert _strip_html_body("") == ""


def test_customer_email_from_ticket() -> None:
    assert (
        _customer_email_from_ticket({"customer": {"email": "User@Example.com"}})
        == "user@example.com"
    )
    assert _customer_email_from_ticket({"customer_email": "a@b.co"}) == "a@b.co"
    assert _customer_email_from_ticket({}) is None


def test_canonical_ticket_customer_first_observation() -> None:
    assert canonical_ticket_customer_email(None, None, "a@b.com", 5) == "a@b.com"


def test_canonical_ticket_customer_rejects_customer_id_change() -> None:
    assert canonical_ticket_customer_email("a@b.com", 1, "a@b.com", 2) is None


def test_canonical_ticket_customer_same_id_keeps_first_email() -> None:
    assert (
        canonical_ticket_customer_email(
            "first@example.com",
            9,
            "second@example.com",
            9,
        )
        == "first@example.com"
    )


def test_canonical_ticket_customer_email_only_rejects_swap() -> None:
    assert canonical_ticket_customer_email("a@b.com", None, "c@d.com", None) is None


def test_group_name_from_ticket() -> None:
    assert _group_name_from_ticket({"group": "Support"}) == "Support"
    assert (
        _group_name_from_ticket(
            {
                "group": {
                    "name": "Users",
                    "active": True,
                }
            }
        )
        == "Users"
    )
    assert _group_name_from_ticket({"group_name": "Escalated"}) == "Escalated"
    assert _group_name_from_ticket({}) is None
