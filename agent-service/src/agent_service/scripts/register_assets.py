"""Asset registration script for agent service."""

import sys

from agent_service.knowledge import KnowledgeBaseManager


def main() -> None:
    """Main entry point for asset registration."""
    # Initialize managers
    kb_manager = KnowledgeBaseManager()

    # Register knowledge bases
    print("Registering knowledge bases...")
    success = kb_manager.register_knowledge_bases()

    if success:
        print("Asset registration completed successfully")
        sys.exit(0)
    else:
        print(
            "Asset registration failed - one or more knowledge bases could not be registered"
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
