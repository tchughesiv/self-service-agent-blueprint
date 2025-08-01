from llama_stack_client.types.shared_params.agent_config import AgentConfig

class Agent:
    def __init__(self, id: str, name: str, description: str, config: AgentConfig):
        self._id = id
        self._name = name
        self._description = description
        self._config = config

    def id(self):
        return self._id
    
    def name(self):
        return self._name
    
    def description(self):
        return self._description
    
    def config(self):
        return self._config