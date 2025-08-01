import logging
import os

from llama_stack_client import LlamaStackClient
from llama_stack_client.types.shared_params.agent_config import AgentConfig
from agent_manager.agent import Agent

class AgentManager:
    def __init__(self, config):
        self._config = config
        self._client = None
        self._agents = []

    def connect_to_llama_stack(self):
        if self._client is None:
            logging.debug("Connecting to LlamaStack")
            self._client = LlamaStackClient(
                base_url=os.environ["LLAMASTACK_SERVICE_HOST"],
                timeout=self._config['timeout'],
            )
        else:
            logging.debug("Already connected to LlamaStack")

    def create_agents(self):
        if self._client is None:
            self.connect_to_llama_stack()
        if self._agents == []:
            logging.debug("Creating agents")
            for agent in self._config["agents"]:
                self.create_agent(agent)
        else:
            logging.debug("Agents already created")

    def create_agent(self, agent: dict):
        agent_config:AgentConfig={
            "model": agent['model'],
            "instructions": agent['instructions'],
            "tool_choice": agent['tool_choice'],
            "input_shields": agent['input_shields'],
            "output_shields": agent['output_shields'],
            "max_infer_iters": agent['max_infer_iters'],
        }
        agentic_system_create_response = self._client.agents.create(
            agent_config=agent_config
        )
        agent_id = agentic_system_create_response.agent_id
        self._agents.append(Agent(agent_id, agent['name'], agent['description'], agent_config))

    def config(self):
        return self._config
    
    def is_connected(self):
        return self._client is not None

    def agents(self):
        return self._agents

    