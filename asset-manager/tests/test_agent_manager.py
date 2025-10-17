from pathlib import Path

import pytest
from asset_manager.agent_manager import AgentManager
from asset_manager.util import load_config_from_path


@pytest.fixture
def test_subject() -> AgentManager:
    config_path = Path("local_testing/config")
    config = load_config_from_path(config_path)
    agent_manager = AgentManager(config)
    agent_manager.set_config_path(config_path)  # Set config path for prompt loading
    return agent_manager


def test_agent_manager(test_subject: AgentManager) -> None:
    config = test_subject.config()
    assert config is not None
    assert config["timeout"] == 120.0
    assert len(config["agents"]) == 1
    assert config["agents"][0]["name"] == "agent-1"
    assert config["agents"][0]["model"] == "llama3.2:3b"
    assert config["agents"][0]["instructions"] == "You are a helpful agent"
