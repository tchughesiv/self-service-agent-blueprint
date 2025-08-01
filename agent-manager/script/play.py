from agent_manager.agent_manager import AgentManager

def main():
    agent_manager = AgentManager()
    print('config:', agent_manager.config())
    print('is_connected:', agent_manager.is_connected())
    print('agents:', agent_manager.agents())

    print('connecting to llama stack...')
    agent_manager.connect_to_llama_stack()
    print('is_connected:', agent_manager.is_connected())

    print('creating agents...')
    agent_manager.create_agents()
    print('agents:', agent_manager.agents())

if __name__ == "__main__":
    main()