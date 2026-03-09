"""Test defaults for integration-dispatcher (SlackService / ZammadService need BROKER_URL at import)."""

import os

os.environ.setdefault("BROKER_URL", "http://mock-broker.test/broker")
