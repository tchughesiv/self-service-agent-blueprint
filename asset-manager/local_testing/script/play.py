from pathlib import Path

from asset_manager.agent_manager import AgentManager
from asset_manager.util import load_config_from_path


def main() -> None:
    config_path = Path("local_testing/config")
    config = load_config_from_path(config_path)
    agent_manager = AgentManager(config)
    agent_manager.set_config_path(config_path)  # Set config path for prompt loading
    print("config:", agent_manager.config())
    print("is_connected:", agent_manager.is_connected())

    print("connecting to llama stack...")
    agent_manager.connect_to_llama_stack()
    print("is_connected:", agent_manager.is_connected())

    print("deleting agents...")
    agent_manager.delete_agents()
    print("agents", len(agent_manager.agents().data))

    print("creating agents...")
    agent_manager.create_agents()
    print("agents", len(agent_manager.agents().data))


if __name__ == "__main__":
    main()
