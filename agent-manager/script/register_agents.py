from agent_manager.agent_manager import AgentManager
from agent_manager.kb_manager import KnowledgeBaseManager
from agent_manager.util import load_config_from_path
from pathlib import Path


def main():
    config = load_config_from_path(Path("config"))
    
    # Initialize managers
    agent_manager = AgentManager(config)
    kb_manager = KnowledgeBaseManager(config)
    
    print("config:", agent_manager.config())
    
    # Connect to llama stack
    print("connecting to llama stack...")
    agent_manager.connect_to_llama_stack()
    kb_manager.connect_to_llama_stack()
    
    print("agent_manager is_connected:", agent_manager.is_connected())
    print("kb_manager is_connected:", kb_manager.is_connected())
    
    # Create knowledge bases first (agents may depend on them)
    print("creating knowledge bases...")
    kb_manager.create_knowledge_bases()
    
    # Then create agents
    print("creating agents...")
    agent_manager.create_agents()


if __name__ == "__main__":
    main()
