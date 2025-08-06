import logging
import os
import httpx
import json

from llama_stack_client import LlamaStackClient
from llama_stack_client.types.shared_params.agent_config import AgentConfig


def toolgroups(agent):
    toolgroups = []
    if "mcp_servers" in agent:
        for mcp_server in agent["mcp_servers"]:
            toolgroups.append("mcp::" + mcp_server)

    if "knowledge_bases" in agent:
        toolgroups.append(
            {
                "name": "builtin::rag/knowledge_search",
                "args": {"vector_db_ids": agent["knowledge_bases"]},
            }
        )

    return toolgroups


class AgentManager:
    def __init__(self, config):
        self._config = config
        self._client = None
        self._agents = []

    def connect_to_llama_stack(self):
        if self._client is None:
            logging.debug("Connecting to LlamaStack")
            llama_stack_host = os.environ["LLAMASTACK_SERVICE_HOST"]
            self._client = LlamaStackClient(
                base_url=f"http://{llama_stack_host}:8321",
                timeout=self._config["timeout"],
            )
        else:
            logging.debug("Already connected to LlamaStack")

    def create_agents(self):
        if self._client is None:
            self.connect_to_llama_stack()
        logging.debug("Creating agents")
        for agent in self._config["agents"]:
            self.create_agent(agent)

    def create_agent(self, agent: dict):
        agent_config: AgentConfig = {
            "name": agent["name"],
            "model": agent["model"],
            "instructions": agent["instructions"],
            "tool_choice": agent["tool_choice"],
            "input_shields": agent["input_shields"],
            "output_shields": agent["output_shields"],
            "max_infer_iters": agent["max_infer_iters"],
            "toolgroups": toolgroups(agent),
        }

        # if no model was specified use the first deployed model
        if not agent_config["model"]:
            agent_config["model"] = os.environ["LLAMA_STACK_MODELS"].split(",", 1)[0]

        agentic_system_create_response = self._client.agents.create(
            agent_config=agent_config
        )
        return agentic_system_create_response.agent_id

    def delete_agents(self):
        if self._client is None:
            self.connect_to_llama_stack()
        logging.debug("Deleting agents")
        agents = self.agents().data
        for agent in agents:
            self._client.agents.delete(agent["agent_id"])

    def config(self):
        return self._config

    def is_connected(self):
        return self._client is not None

    def agents(self):
        if self._client is None:
            self.connect_to_llama_stack()

        # there does not seem to be a list method available do it manually
        response = self._client.get("v1/agents", cast_to=httpx.Response)
        json_string = response.content.decode("utf-8")
        data = json.loads(json_string)

        # create ditionay with agent name as key
        agents = {}
        for agent in data["data"]:
            if isinstance(agent, dict):
                agent_id = agent.get("agent_id")
                agent_config = agent.get("agent_config", {})
                agent_name = agent_config.get(
                    "name", f"Agent_{agent_id[:8]}" if agent_id else "Unknown"
                )

                if agent_name and agent_id:
                    agents[agent_name] = agent_id

        return agents
