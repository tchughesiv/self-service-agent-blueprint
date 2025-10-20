import json
import logging
from pathlib import Path
from typing import Any

import httpx
from llama_stack_client.lib.agents.agent import AgentUtils
from llama_stack_client.types.agents.session_create_response import (
    SessionCreateResponse,
)
from llama_stack_client.types.shared.agent_config import ToolConfig
from llama_stack_client.types.shared_params.sampling_params import SamplingParams

from .manager import Manager


def toolgroups(agent: dict[str, Any]) -> list[Any] | None:
    if not agent.get("mcp_servers") and not agent.get("knowledge_bases"):
        return None

    toolgroups: list[Any] = []
    kbs = agent.get("knowledge_bases")
    if kbs:
        toolgroups.append(
            {
                "name": "builtin::rag/knowledge_search",
                "args": {"vector_db_ids": kbs},
            }
        )

    mcp_servers = agent.get("mcp_servers")
    if mcp_servers:
        for mcp_server in mcp_servers:
            toolgroups.append("mcp::" + mcp_server)

    return toolgroups


def tool_config(agent: dict[str, Any]) -> ToolConfig | None:
    if not agent.get("tool_choice"):
        return None
    return ToolConfig(tool_choice=agent["tool_choice"])


def sampling_params(agent: dict[str, Any]) -> SamplingParams | None:
    """Extract and format sampling parameters from agent config using proper SamplingParams type."""
    if not agent.get("sampling_params"):
        return None

    sampling_config = agent["sampling_params"]

    # Strategy is required for SamplingParams, so check first
    if "strategy" not in sampling_config:
        return None

    # Build the sampling parameters structure using proper SamplingParams type
    sampling_params_dict: SamplingParams = {"strategy": sampling_config["strategy"]}

    # Handle max_tokens
    if "max_tokens" in sampling_config:
        sampling_params_dict["max_tokens"] = sampling_config["max_tokens"]

    # Handle repetition_penalty if present
    if "repetition_penalty" in sampling_config:
        sampling_params_dict["repetition_penalty"] = sampling_config[
            "repetition_penalty"
        ]

    # Handle stop tokens if present
    if "stop" in sampling_config:
        sampling_params_dict["stop"] = sampling_config["stop"]

    return sampling_params_dict


class AgentManager(Manager):
    def __init__(self, config: dict[str, Any]) -> None:
        self._client = None
        self._config = config
        self._agents: list[Any] = []
        self._config_path: Path | None = (
            None  # Will be set when we know the config path
        )

    def set_config_path(self, config_path: Path) -> None:
        """Set the config path so we can locate prompt files"""
        self._config_path = config_path

    def load_prompt_with_model_suffix(self, agent_name: str, model: str) -> str:
        """Load prompt file with model suffix appended to filename"""
        if not self._config_path:
            raise ValueError("Config path not set. Call set_config_path() first.")

        prompts_path = self._config_path / "prompts"

        # Extract the part after the last / from the model name
        model_suffix = model.split("/")[-1] if "/" in model else model

        # Try to load prompt with model suffix first
        prompt_with_suffix = prompts_path / f"{agent_name}-{model_suffix}.txt"

        if prompt_with_suffix.exists():
            print(f"Loading model-specific prompt: {prompt_with_suffix}")
            with open(prompt_with_suffix, "r") as f:
                return f.read()

        # Fall back to original prompt file
        original_prompt = prompts_path / f"{agent_name}.txt"
        if original_prompt.exists():
            print(f"Loading default prompt: {original_prompt}")
            with open(original_prompt, "r") as f:
                return f.read()

        print(f"No prompt file found for: {model_suffix}")
        raise FileNotFoundError(f"No prompt file found for agent {agent_name}")

    def create_agents(self) -> None:
        if self._client is None:
            self.connect_to_llama_stack()
        logging.debug("Creating agents")
        for agent in self._config["agents"]:
            self.create_agent(agent)

    def create_agent(self, agent: dict[str, Any]) -> str:
        # Get the model name
        model = self.model(agent)

        # Load prompt with model suffix if config path is set, otherwise use pre-loaded instructions
        if self._config_path and model is not None:
            try:
                instructions = self.load_prompt_with_model_suffix(agent["name"], model)
            except FileNotFoundError:
                # Fall back to pre-loaded instructions if no model-specific prompt found
                instructions = agent["instructions"]
        else:
            instructions = agent["instructions"]

        agent_config = AgentUtils.get_agent_config(
            model=model,
            instructions=instructions,
            tools=toolgroups(agent),
            tool_config=tool_config(agent),  # type: ignore[arg-type]
            max_infer_iters=agent["max_infer_iters"],
            input_shields=agent["input_shields"],
            output_shields=agent["output_shields"],
            enable_session_persistence=agent["enable_session_persistence"],
            sampling_params=sampling_params(agent),
        )
        agent_config["name"] = agent["name"]

        if self._client is None:
            logging.error("Client not connected. Cannot create agent.")
            return ""

        agentic_system_create_response = self._client.agents.create(
            agent_config=agent_config
        )
        return agentic_system_create_response.agent_id

    def delete_agents(self) -> None:
        if self._client is None:
            logging.error("Client not connected. Cannot delete agents.")
            return

        logging.debug("Deleting agents")
        agents = self.agents()
        for _, agent_id in agents.items():
            self._client.agents.delete(agent_id)

    def agents(self) -> dict[str, str]:
        if self._client is None:
            self.connect_to_llama_stack()

        if self._client is None:
            logging.error("Client not connected. Cannot list agents.")
            return {}

        # there does not seem to be a list method available do it manually
        response = self._client.get("v1/agents", cast_to=httpx.Response)
        json_string = response.content.decode("utf-8")
        data = json.loads(json_string)

        # create dictionary with agent name as key
        agents: dict[str, str] = {}
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

    def model(self, agent: dict[str, Any]) -> str | None:
        if agent.get("model"):
            model = agent["model"]
            print(model)
            return str(model) if model is not None else None

        # Select the first LLM model
        if self._client is None:
            logging.error("Client not connected. Cannot get model.")
            return ""

        models = self._client.models.list()
        model_id = next(m for m in models if m.api_model_type == "llm").identifier
        if model_id:
            print(model_id)
            return model_id
        return None

    def create_session(
        self, agent_id: str, session_name: str
    ) -> SessionCreateResponse | None:
        """Create a new session for an agent"""
        if self._client is None:
            logging.error("Client not connected. Cannot create session.")
            return None

        return self._client.agents.session.create(agent_id, session_name=session_name)

    def create_agent_turn(
        self,
        agent_id: str,
        session_id: str,
        stream: bool = True,
        messages: list[Any] | None = None,
    ) -> Any:
        """Send a turn to an agent"""
        if self._client is None:
            logging.error("Client not connected. Cannot create turn.")
            return None

        return self._client.agents.turn.create(
            agent_id=agent_id,
            session_id=session_id,
            stream=stream,
            messages=messages or [],
        )
