import pytest
from agent_manager.agent_manager import AgentManager

@pytest.fixture
def test_subject():
    return AgentManager('Mike')

def test_agent_manager_name(test_subject: AgentManager):
    assert test_subject.name() == 'Mike'