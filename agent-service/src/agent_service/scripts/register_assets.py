"""Asset registration script for agent service."""

from agent_service.knowledge import KnowledgeBaseManager


def main() -> None:
    """Main entry point for asset registration."""
    # Initialize managers
    kb_manager = KnowledgeBaseManager()

    # Register knowledge bases
    print("Registering knowledge bases...")
    kb_manager.register_knowledge_bases()

    print("Asset registration completed successfully")


if __name__ == "__main__":
    main()
