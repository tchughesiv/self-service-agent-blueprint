from agent_manager.agent_manager import AgentManager
from agent_manager.util import load_config_from_path
from pathlib import Path


def main():
    config = load_config_from_path(Path("config"))
    agent_manager = AgentManager(config)
    print("config:", agent_manager.config())
    print("connecting to llama stack...")
    agent_manager.connect_to_llama_stack()
    print("is_connected:", agent_manager.is_connected())
    print("creating agents...")
    agent_manager.create_agents()


if __name__ == "__main__":
    main()
