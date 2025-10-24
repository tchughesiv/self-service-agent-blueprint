#!/usr/bin/env python3
"""
Script to copy context files to the default_context directory for evaluations.

This script copies:
1. All files from agent-service/config/knowledge_bases/laptop-refresh/
2. The ServiceNow data file from mcp-servers/snow/src/snow/data.py

All files are copied to evaluations/conversations_config/default_context/
"""

import logging
import shutil
from pathlib import Path

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def copy_context_files() -> None:
    """
    Copy context files from source locations to the default_context directory.

    This function copies:
    1. All files from agent-service/config/knowledge_bases/laptop-refresh/
    2. The ServiceNow data file from mcp-servers/snow/src/snow/data.py

    All files are copied to evaluations/conversations_config/default_context/

    The target directory is created if it doesn't exist. Missing source files
    or directories result in warning logs but do not cause the function to fail.

    Raises:
        Exception: If there are critical errors accessing the workspace structure
    """
    # Get the workspace root (assuming this script is in evaluations/helpers/)
    workspace_root = Path(__file__).parent.parent.parent

    # Define source and target paths
    laptop_refresh_source = (
        workspace_root
        / "agent-service"
        / "config"
        / "knowledge_bases"
        / "laptop-refresh"
    )
    snow_data_source = (
        workspace_root / "mcp-servers" / "snow" / "src" / "snow" / "data.py"
    )
    target_dir = (
        workspace_root / "evaluations" / "conversations_config" / "default_context"
    )

    # Create target directory if it doesn't exist
    target_dir.mkdir(parents=True, exist_ok=True)

    # Copy all files from laptop-refresh directory
    if laptop_refresh_source.exists():
        for file_path in laptop_refresh_source.iterdir():
            if file_path.is_file():
                target_file = target_dir / file_path.name
                shutil.copy2(file_path, target_file)
    else:
        logger.warning(f"Source directory not found: {laptop_refresh_source}")

    # Copy the ServiceNow data file
    if snow_data_source.exists():
        target_file = target_dir / "snow_data.py"
        shutil.copy2(snow_data_source, target_file)
    else:
        logger.warning(f"ServiceNow data file not found: {snow_data_source}")


if __name__ == "__main__":
    copy_context_files()
