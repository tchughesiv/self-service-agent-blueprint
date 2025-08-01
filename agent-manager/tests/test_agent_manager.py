import pytest
from agent_manager.agent_manager import AgentManager
from agent_manager.util import load_yaml

@pytest.fixture
def test_subject():
    return AgentManager(load_yaml("config/agents/config.yaml"))

def test_agent_manager(test_subject: AgentManager):
    config = test_subject.config()
    assert config is not None
    assert config['timeout'] == 120.0
    assert len(config['agents']) == 1
    assert config['agents'][0]['name'] == "agent-1"
    assert config['agents'][0]['model'] == "llama3.2:3b"
    assert config['agents'][0]['instructions'] == "You are a helpful agent"