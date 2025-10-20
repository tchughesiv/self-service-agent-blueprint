#!/usr/bin/env python3
"""
LangGraph-based state machine and agent session management.

This module contains the StateMachine and AgentSession classes for managing
conversational flows using LangGraph with persistent checkpoint storage.
"""
import logging
from pathlib import Path
from typing import Annotated, Any, Dict, List, Optional, TypedDict

import yaml
from asset_manager.util import resolve_asset_manager_path
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Command

# Import SqliteSaver - will be replace by more persistent state storage
try:
    from langgraph_checkpoint_sqlite import SqliteSaver  # type: ignore
except ImportError:
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError:
        # Minimal fallback for development environments
        SqliteSaver = None

logger = logging.getLogger(__name__)


# Dynamic state definition created from YAML configuration
def create_agent_state_class(state_schema: dict[str, Any]) -> Any:
    """Create a dynamic AgentState TypedDict class based on YAML configuration."""
    # Collect all field definitions
    fields = {}

    # Add required system fields automatically (no need to define in YAML)
    fields["messages"] = Annotated[List[BaseMessage], add_messages]
    fields["current_state"] = str

    # Handle case where state_schema is None or empty
    if not state_schema:
        state_schema = {}

    # Add business fields
    business_fields = state_schema.get("business_fields", {})
    for field_name, field_config in business_fields.items():
        field_type = field_config.get("type", "string")

        if field_type == "string":
            fields[field_name] = Optional[str]
        elif field_type == "list":
            fields[field_name] = List[Dict[str, Any]]
        elif field_type == "dict":
            fields[field_name] = Optional[Dict[str, Any]]
        elif field_type == "boolean":
            fields[field_name] = Optional[bool]
        else:
            fields[field_name] = Optional[str]  # Default to optional string

    # Add internal tracking fields for waiting node logic
    fields["_last_processed_human_count"] = Optional[int]
    fields["_consumed_this_invoke"] = Optional[bool]
    fields["_last_waiting_node"] = Optional[str]  # Track last waiting node for resume

    # Create the TypedDict class dynamically
    return TypedDict("AgentState", fields)  # type: ignore[operator]


class StateMachine:
    """Configurable state machine engine for conversation flows."""

    def __init__(self, config_path: str):
        """Initialize the state machine with configuration from YAML file."""
        self.config_path = Path(config_path)
        self.config = self._load_config()

        # Create dynamic AgentState class from configuration
        state_schema = self.config.get("state_schema", {})
        self.AgentState = create_agent_state_class(state_schema)

    def _load_config(self) -> dict[str, Any]:
        """Load state machine configuration from YAML file."""
        try:
            with open(self.config_path, "r") as f:
                return yaml.safe_load(f)
        except Exception as e:
            raise RuntimeError(
                f"Failed to load state machine config from {self.config_path}: {e}"
            )

    def _get_retry_count(self) -> int:
        """Get the configured retry count for empty responses."""
        settings = self.config.get("settings", {})
        return settings.get("empty_response_retry_count", 3)

    def _is_config_disabled(self, config_value) -> bool:  # type: ignore[no-untyped-def]
        """Check if a config value represents 'disabled' (no/No/NO or False).

        Args:
            config_value: The value from YAML config (can be bool, str, or other)

        Returns:
            True if the value represents disabled (False boolean or "no" string)
        """
        return (isinstance(config_value, bool) and not config_value) or (
            isinstance(config_value, str) and config_value.lower() == "no"
        )

    def _build_response_kwargs(
        self,
        state: dict[str, Any],
        state_config: dict[str, Any],
        temperature: float,
        authoritative_user_id: str,
        allowed_tools: list[str] | None = None,
        action_config: dict[str, Any] | None = None,
        token_context: str | None = None,
    ) -> dict[str, Any]:
        """Build kwargs for create_response_with_retry.

        Args:
            state: Current state dictionary
            state_config: State configuration from YAML
            temperature: Temperature setting
            authoritative_user_id: User ID for authorization
            allowed_tools: Optional list of allowed tools
            action_config: Optional action config (for intent actions), falls back to state_config

        Returns:
            Dictionary of kwargs for create_response_with_retry
        """
        # Check if uses_tools/uses_mcp_tools are disabled (with state-level fallback for actions)
        if action_config:
            skip_all_tools = self._is_config_disabled(
                action_config.get("uses_tools", state_config.get("uses_tools", "yes"))
            )
            skip_mcp_servers_only = self._is_config_disabled(
                action_config.get(
                    "uses_mcp_tools", state_config.get("uses_mcp_tools", "yes")
                )
            )
        else:
            skip_all_tools = self._is_config_disabled(
                state_config.get("uses_tools", "yes")
            )
            skip_mcp_servers_only = self._is_config_disabled(
                state_config.get("uses_mcp_tools", "yes")
            )

        # Build kwargs
        response_kwargs = {
            "temperature": temperature,
            "authoritative_user_id": authoritative_user_id,
            "skip_all_tools": skip_all_tools,
            "skip_mcp_servers_only": skip_mcp_servers_only,
            "current_state_name": state.get("current_state"),
        }

        # Add allowed_tools if specified
        if allowed_tools is not None:
            response_kwargs["allowed_tools"] = allowed_tools

        # Add token_context if specified
        if token_context is not None:
            response_kwargs["token_context"] = token_context

        return response_kwargs

    def _format_text(
        self,
        text: str,
        state_data: dict[str, Any],
        authoritative_user_id: str | None = None,
    ) -> str:
        """Format text by replacing placeholders with state data."""
        import re

        try:
            # Add special computed values
            format_data = dict(state_data)

            # Add authoritative_user_id if provided
            if authoritative_user_id:
                format_data["authoritative_user_id"] = authoritative_user_id

            # Add last_user_message placeholder
            messages = state_data.get("messages", [])
            last_user_message = ""
            for msg in reversed(messages):
                if (
                    hasattr(msg, "content")
                    and getattr(msg, "__class__", None).__name__ == "HumanMessage"
                ):
                    last_user_message = msg.content
                    break
            format_data["last_user_message"] = last_user_message

            # Add conversation_history placeholder
            conversation_history = ""
            for i, msg in enumerate(messages):
                if hasattr(msg, "content"):
                    msg_type = getattr(msg, "__class__", None).__name__
                    if msg_type == "HumanMessage":
                        conversation_history += f"User: {msg.content}\n"
                    elif msg_type == "AIMessage":
                        conversation_history += f"Assistant: {msg.content}\n"
            format_data["conversation_history"] = conversation_history.strip()

            # Custom dot notation replacement
            def replace_placeholders(text, data):  # type: ignore[no-untyped-def]
                # First, temporarily replace escaped braces to protect them
                text = text.replace("{{", "\x00ESCAPED_OPEN\x00")
                text = text.replace("}}", "\x00ESCAPED_CLOSE\x00")

                # Pattern to match {field.subfield} or {field} (but not escaped ones)
                pattern = r"\{([^}]+)\}"

                def replacer(match):  # type: ignore[no-untyped-def]
                    field_path = match.group(1)
                    try:
                        # Split by dots and navigate the data structure
                        value = data
                        for part in field_path.split("."):
                            if isinstance(value, dict):
                                value = value[part]
                            else:
                                value = getattr(value, part)
                        return str(value)
                    except (KeyError, AttributeError, TypeError):
                        # Return the placeholder unchanged if field not found
                        logger.warning(f"Missing placeholder data for {field_path}")
                        return match.group(0)

                # Replace placeholders
                text = re.sub(pattern, replacer, text)

                # Restore escaped braces as single braces
                text = text.replace("\x00ESCAPED_OPEN\x00", "{")
                text = text.replace("\x00ESCAPED_CLOSE\x00", "}")

                return text

            return replace_placeholders(text, format_data)

        except Exception as e:
            logger.warning(f"Error formatting text: {e}, returning original text")
            return text

    def process_llm_processor_state(
        self,
        state: dict[str, Any],
        state_config: dict[str, Any],
        agent: Any,
        authoritative_user_id: str | None = None,
        token_context: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Process llm_processor type states - completely generic and configuration-driven.

        Returns:
            tuple: (updated_state, next_state_name)
        """

        # Step 1: Determine the prompt to use
        prompt = self._get_prompt_for_state(state, state_config, authoritative_user_id)

        # Step 2: Prepare messages and send to LLM with retry logic for empty responses
        # Check if state config wants to use conversation history
        use_conversation_history = state_config.get("use_conversation_history", False)

        if use_conversation_history:
            # Convert state messages to API format and add current prompt as system message
            messages_to_send = []

            # Add the prompt as a system message
            messages_to_send.append({"role": "system", "content": prompt})

            # Add conversation history
            state_messages = state.get("messages", [])
            logger.info(
                f"Using conversation history with {len(state_messages)} messages"
            )
            for msg in state_messages:
                if hasattr(msg, "content"):
                    msg_type = getattr(msg, "__class__", None).__name__
                    if msg_type == "HumanMessage":
                        messages_to_send.append(
                            {"role": "user", "content": msg.content}
                        )
                    elif msg_type == "AIMessage":
                        messages_to_send.append(
                            {"role": "assistant", "content": msg.content}
                        )

            logger.info(
                f"Sending {len(messages_to_send)} messages to LLM (1 system + {len(messages_to_send) - 1} conversation)"
            )
        else:
            # Traditional approach - send prompt as single user message
            messages_to_send = [{"role": "user", "content": prompt}]
            logger.info(
                "Using traditional approach - sending prompt as single user message"
            )

        temperature = state_config.get("temperature")
        allowed_tools = state_config.get("allowed_tools")

        # Build kwargs for create_response_with_retry
        # Use token_context parameter passed from ConversationSession
        response_kwargs = self._build_response_kwargs(
            state,
            state_config,
            temperature,
            authoritative_user_id,
            allowed_tools,
            token_context=token_context,
        )

        response = agent.create_response_with_retry(
            messages_to_send,
            self._get_retry_count(),
            **response_kwargs,
        )

        # Step 3: Store response data as configured
        self._store_response_data(state, state_config, response)

        # Step 4: Add response to conversation
        state["messages"].append(AIMessage(content=response))

        # Step 5: Analyze response and determine next state
        next_state = self._analyze_response_and_transition(
            state, state_config, response, authoritative_user_id
        )

        return state, next_state

    def _get_prompt_for_state(
        self,
        state: dict[str, Any],
        state_config: dict[str, Any],
        authoritative_user_id: str | None = None,
    ) -> str:
        """Get the appropriate prompt based on conditional logic or default."""

        # Check for conditional prompts
        conditional_prompts = state_config.get("conditional_prompts")
        if conditional_prompts:
            for condition_config in conditional_prompts:
                condition_name = condition_config.get("condition")

                if condition_name == "default":
                    continue  # Handle default last

                # Check if condition is met
                if self._evaluate_condition(state, condition_config):
                    prompt_text = condition_config.get("prompt", "")
                    return self._format_text(prompt_text, state, authoritative_user_id)

            # If no conditions matched, use default
            for condition_config in conditional_prompts:
                if condition_config.get("condition") == "default":
                    prompt_text = condition_config.get("prompt", "")
                    return self._format_text(prompt_text, state, authoritative_user_id)

        # Fallback to simple prompt
        prompt_text = state_config.get("prompt", "")
        return self._format_text(prompt_text, state, authoritative_user_id)

    def _evaluate_condition(
        self, state: dict[str, Any], condition_config: dict[str, Any]
    ) -> bool:
        """Evaluate whether a condition is met based on state data."""
        check_field = condition_config.get("check_field")
        check_phrases = condition_config.get("check_phrases", [])
        check_empty = condition_config.get("check_empty")

        # Handle empty/non-empty checks
        if check_field and check_empty is not None:
            field_value = self._get_nested_field_value(state, check_field)
            if check_empty:
                # Check if field is empty (None, empty list, empty string)
                return not field_value or (
                    isinstance(field_value, (list, str)) and len(field_value) == 0
                )
            else:
                # Check if field is not empty
                return bool(
                    field_value
                    and (
                        not isinstance(field_value, (list, str)) or len(field_value) > 0
                    )
                )

        # Handle phrase-based checks
        if not check_field or not check_phrases:
            return False

        # Navigate to the field value (supports dot notation like "laptop_eligibility.response")
        field_value = self._get_nested_field_value(state, check_field)
        if not field_value:
            return False

        # Check if any of the phrases are present
        field_value_lower = str(field_value).lower()
        return any(phrase.lower() in field_value_lower for phrase in check_phrases)

    def _get_nested_field_value(self, state: dict[str, Any], field_path: str) -> Any:
        """Get a nested field value using dot notation (e.g., 'laptop_eligibility.response')."""
        try:
            value = state
            for part in field_path.split("."):
                if isinstance(value, dict):
                    value = value.get(part)
                else:
                    return None
            return value
        except (KeyError, TypeError, AttributeError):
            return None

    def _store_response_data(
        self, state: dict[str, Any], state_config: dict[str, Any], response: str
    ) -> None:
        """Store response data according to configuration."""
        data_storage = state_config.get("data_storage", {})
        for key, source in data_storage.items():
            if source == "llm_response":
                state[key] = {"response": response}

    def _analyze_response_and_transition(
        self,
        state: dict[str, Any],
        state_config: dict[str, Any],
        response: str,
        authoritative_user_id: str | None = None,
    ) -> str:
        """Analyze LLM response and determine next state transition."""

        # Check for response analysis configuration
        response_analysis = state_config.get("response_analysis")
        if response_analysis:
            return self._process_response_analysis(
                state, response_analysis, response, authoritative_user_id
            )

        # Fallback to simple transitions
        transitions = state_config.get("transitions", {})
        return transitions.get("success", "end")

    def _process_response_analysis(
        self,
        state: dict[str, Any],
        analysis_config: dict[str, Any],
        response: str,
        authoritative_user_id: str | None = None,
    ) -> str:
        """Process response analysis with conditions and actions."""
        response_lower = response.lower()

        conditions = analysis_config.get("conditions", [])
        for condition in conditions:
            trigger_phrases = condition.get("trigger_phrases", [])
            exclude_phrases = condition.get("exclude_phrases", [])

            # Check if trigger phrases are present
            has_trigger = any(
                phrase.lower() in response_lower for phrase in trigger_phrases
            )
            if not has_trigger:
                continue

            # Check if exclude phrases are present (if so, skip this condition)
            has_exclude = any(
                phrase.lower() in response_lower for phrase in exclude_phrases
            )
            if has_exclude:
                continue

            # Execute actions for this condition - completely generic
            actions = condition.get("actions", [])
            next_state = self._execute_actions(
                state, actions, response, response_lower, authoritative_user_id
            )

            if next_state:
                return next_state

        # If no conditions matched, use default transition
        return analysis_config.get("default_transition", "end")

    def _execute_actions(
        self,
        state: dict[str, Any],
        actions: list[dict[str, Any]],
        response: str,
        response_lower: str,
        authoritative_user_id: str | None = None,
    ) -> str:
        """Execute a list of actions completely generically based on configuration."""
        next_state = None

        for action in actions:
            action_type = action.get("type")

            if action_type == "transition":
                next_state = action.get("target", "end")

            elif action_type == "check_correction":
                # Check if correction is needed based on phrases
                correction_phrases = action.get("correction_phrases", [])
                if any(
                    phrase.lower() in response_lower for phrase in correction_phrases
                ):
                    correction_message = action.get("correction_message", "")
                    if correction_message:
                        formatted_message = self._format_text(
                            correction_message, state, authoritative_user_id
                        )
                        state["messages"].append(AIMessage(content=formatted_message))

            elif action_type == "extract_data":
                # Generic data extraction using regex patterns
                pattern = action.get("pattern", "")
                field_name = action.get("field_name", "")
                source_text = action.get(
                    "source", "response"
                )  # "response" or "last_user_message"

                if pattern and field_name:
                    import re

                    if source_text == "response":
                        text_to_search = response
                    elif source_text == "last_user_message":
                        text_to_search = self._get_last_user_message(state)
                    else:
                        text_to_search = response

                    match = re.search(pattern, text_to_search)
                    if match:
                        # Use group(1) if available (captured group), otherwise group(0) (entire match)
                        try:
                            state[field_name] = match.group(1)
                        except IndexError:
                            state[field_name] = match.group(0)

            elif action_type == "add_message":
                # Add a message to the conversation
                message_content = action.get("message", "")
                if message_content:
                    formatted_message = self._format_text(
                        message_content, state, authoritative_user_id
                    )
                    state["messages"].append(AIMessage(content=formatted_message))

            elif action_type == "set_field":
                # Set a field value
                field_name = action.get("field_name", "")
                field_value = action.get("value")
                if field_name:
                    state[field_name] = field_value

        return next_state

    def _get_last_user_message(self, state: dict[str, Any]) -> str:
        """Get the last user message from the conversation."""
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if (
                hasattr(msg, "content")
                and getattr(msg, "__class__", None).__name__ == "HumanMessage"
            ):
                return msg.content
        return ""

    def is_terminal_state(self, state_name: str) -> bool:
        """Check if the given state is a terminal state."""
        settings = self.config.get("settings", {})
        terminal_state = settings.get("terminal_state", "end")
        return state_name == terminal_state

    def is_waiting_state(self, state_name: str) -> bool:
        """Check if the given state is a waiting state by looking up its type."""
        states = self.config.get("states", {})
        state_config = states.get(state_name, {})
        return state_config.get("type") == "waiting"

    def create_initial_state(self) -> dict[str, Any]:
        """Create initial state with default field values from configuration."""
        settings = self.config.get("settings", {})
        initial_state_name = settings.get("initial_state", "collect_employee_id")
        state_schema = self.config.get("state_schema", {})

        # Create state with default values from schema
        state: Dict[str, Any] = {}

        # Add required system fields automatically
        state["messages"] = []
        state["current_state"] = initial_state_name

        # Add business fields
        business_fields = state_schema.get("business_fields", {})
        for field_name, field_config in business_fields.items():
            default_value = field_config.get("default")
            if default_value == "null" or default_value is None:
                if field_config.get("type") == "list":
                    state[field_name] = []
                else:
                    state[field_name] = None
            elif default_value == "false":
                state[field_name] = False
            elif default_value == "true":
                state[field_name] = True
            else:
                state[field_name] = default_value

        # Add initial user message if configured
        initial_user_message = settings.get("initial_user_message")
        if initial_user_message and "messages" in state:
            from langchain_core.messages import HumanMessage

            state["messages"].append(HumanMessage(content=initial_user_message))
            # Mark this kickoff message as already processed so waiting nodes don't consume it
            state["_last_processed_human_count"] = 1

        return state

    def reset_state_for_new_conversation(self) -> dict[str, Any]:
        """Reset state for a new conversation based on end state configuration."""
        end_state_config = self.config.get("states", {}).get("end", {})
        reset_behavior = end_state_config.get("reset_behavior", {})
        settings = self.config.get("settings", {})
        state_schema = self.config.get("state_schema", {})

        # Get reset state name - defaults to initial_state if not specified
        reset_state = reset_behavior.get(
            "reset_state",
            settings.get("initial_state", "collect_employee_id"),
        )

        # Get fields to clear from reset behavior or use all fields
        fields_to_clear = reset_behavior.get("clear_data", [])
        if not fields_to_clear:
            # If no specific fields listed, clear required system fields + all business fields
            all_fields = ["messages", "current_state"] + list(
                state_schema.get("business_fields", {}).keys()
            )
            fields_to_clear = all_fields

        # Create new state using schema defaults
        state: Dict[str, Any] = {}

        # Handle required system fields
        if "messages" in fields_to_clear:
            state["messages"] = []
        if "current_state" in fields_to_clear:
            state["current_state"] = reset_state

        # Reset business fields
        business_fields = state_schema.get("business_fields", {})
        for field_name in fields_to_clear:
            if field_name in business_fields:
                field_config = business_fields[field_name]
                default_value = field_config.get("default")
                if default_value == "false":
                    state[field_name] = False
                elif default_value == "true":
                    state[field_name] = True
                elif field_config.get("type") == "list":
                    state[field_name] = []
                else:
                    state[field_name] = None

        return state

    def process_intent_classifier_state(
        self,
        state: dict[str, Any],
        state_config: dict[str, Any],
        agent: Any,
        authoritative_user_id: str | None = None,
        token_context: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Process intent_classifier type states.

        NOTE: Intent classifiers should always be preceded by a waiting state in the YAML.
        They expect a HumanMessage to classify. If no message exists, this is a configuration error.

        Returns:
            tuple: (updated_state, next_state_name)
        """
        messages = state["messages"]
        last_message = messages[-1] if messages else None

        if not last_message or not isinstance(last_message, HumanMessage):
            # This shouldn't happen with proper YAML configuration
            # Intent classifiers should always have a waiting state before them
            logger.error(
                f"Intent classifier '{state.get('current_state')}' reached without user input. "
                "Check YAML configuration - intent_classifiers need a preceding waiting state."
            )
            return state, "end"

        user_input = last_message.content.strip()

        # Use LLM to classify intent
        intent_prompt = self._format_text(
            state_config.get("intent_prompt", ""),
            {**state, "user_input": user_input},
            authoritative_user_id,
        )
        intent_messages = [{"role": "user", "content": intent_prompt}]
        temperature = state_config.get("temperature")
        allowed_tools = state_config.get("allowed_tools")

        # Build kwargs for create_response_with_retry
        response_kwargs = self._build_response_kwargs(
            state,
            state_config,
            temperature,
            authoritative_user_id,
            allowed_tools,
            token_context=token_context,
        )

        intent_response = (
            agent.create_response_with_retry(
                intent_messages,
                self._get_retry_count(),
                **response_kwargs,
            )
            .strip()
            .upper()
        )

        # Process intent actions
        intent_actions = state_config.get("intent_actions", {})
        for intent_name, action in intent_actions.items():
            if isinstance(intent_name, str) and intent_name in intent_response:
                # Handle response
                if "response" in action:
                    response = self._format_text(
                        action["response"],
                        {**state, "user_input": user_input},
                        authoritative_user_id,
                    )
                    state["messages"].append(AIMessage(content=response))

                # Handle LLM prompt
                if "prompt" in action:
                    prompt = self._format_text(
                        action["prompt"],
                        {**state, "user_input": user_input},
                        authoritative_user_id,
                    )
                    messages_to_send = [{"role": "user", "content": prompt}]
                    # Use state temperature for intent action prompts for consistency
                    action_temperature = state_config.get("temperature", 0.6)
                    # Use action-level allowed_tools if specified, otherwise use state-level
                    action_allowed_tools = action.get("allowed_tools", allowed_tools)

                    # Build kwargs for create_response_with_retry (with action-level config)
                    action_response_kwargs = self._build_response_kwargs(
                        state,
                        state_config,
                        action_temperature,
                        authoritative_user_id,
                        action_allowed_tools,
                        action_config=action,
                        token_context=token_context,
                    )

                    response = agent.create_response_with_retry(
                        messages_to_send,
                        self._get_retry_count(),
                        **action_response_kwargs,
                    )
                    state["messages"].append(AIMessage(content=response))

                # Handle data storage
                if "data_storage" in action:
                    for key, value in action["data_storage"].items():
                        # Format the value to substitute placeholders like {user_input}
                        formatted_value = self._format_text(
                            str(value),
                            {**state, "user_input": user_input},
                            authoritative_user_id,
                        )
                        state[key] = formatted_value

                # Return with next state
                next_state = action.get("next_state", "end")
                return state, next_state

        # Default fallback if no intent matched - this shouldn't happen
        logger.warning(f"No intent matched for state {state.get('current_state')}")
        return state, "end"

    def process_llm_validator_state(
        self,
        state: dict[str, Any],
        state_config: dict[str, Any],
        agent: Any,
        authoritative_user_id: str | None = None,
        token_context: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Process llm_validator type states (like laptop selection validation).

        Returns:
            tuple: (updated_state, next_state_name)
        """
        messages = state["messages"]
        last_message = messages[-1] if messages else None

        if not last_message or not isinstance(last_message, HumanMessage):
            return state

        user_input = last_message.content.strip()

        # Use LLM to validate the input
        # Add user_input to state for formatting
        format_state = dict(state)
        format_state["user_input"] = user_input
        validation_prompt = self._format_text(
            state_config.get("validation_prompt", ""),
            format_state,
            authoritative_user_id,
        )

        # Build conversation context
        messages_to_send = []
        for msg in state["messages"]:
            if isinstance(msg, HumanMessage):
                messages_to_send.append({"role": "user", "content": msg.content})
            elif isinstance(msg, AIMessage):
                messages_to_send.append({"role": "assistant", "content": msg.content})

        messages_to_send.append({"role": "system", "content": validation_prompt})
        temperature = state_config.get("temperature", 0.3)  # Default for validation
        allowed_tools = state_config.get("allowed_tools")

        # Build kwargs for create_response_with_retry
        response_kwargs = self._build_response_kwargs(
            state,
            state_config,
            temperature,
            authoritative_user_id,
            allowed_tools,
            token_context=token_context,
        )

        response = agent.create_response_with_retry(
            messages_to_send,
            self._get_retry_count(),
            **response_kwargs,
        )

        # Use LLM to determine if validation passed
        success_validation_prompt = self._format_text(
            state_config.get("success_validation_prompt", ""),
            {"llm_response": response},
            authoritative_user_id,
        )
        validation_messages = [{"role": "user", "content": success_validation_prompt}]

        # Build kwargs for validation response
        validation_kwargs = self._build_response_kwargs(
            state,
            state_config,
            0.1,
            authoritative_user_id,
            allowed_tools,
            token_context=token_context,
        )

        validation_response = (
            agent.create_response_with_retry(
                validation_messages,
                self._get_retry_count(),
                **validation_kwargs,
            )
            .strip()
            .upper()
        )

        # Store data and transition
        data_storage = state_config.get("data_storage", {})
        for key, source in data_storage.items():
            if source == "user_input":
                state[key] = user_input

        transitions = state_config.get("transitions", {})
        if "VALID" in validation_response:
            next_state = transitions.get("valid", "end")
        else:
            next_state = transitions.get("invalid", "waiting_laptop_selection")

        state["messages"].append(AIMessage(content=response))
        return state, next_state

    def process_terminal_state(
        self, state: dict[str, Any], state_config: dict[str, Any]
    ) -> tuple[dict[str, Any], str]:
        """Process terminal type states.

        Returns:
            tuple: (updated_state, next_state_name) - always returns "end"
        """
        return state, "end"

    def process_state(
        self,
        state: dict[str, Any],
        agent: Any,
        authoritative_user_id: str | None = None,
        token_context: str | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Process the current state based on its configuration.

        Returns:
            tuple: (updated_state, next_state_name)
        """
        current_state_name = state.get("current_state", "")
        state_config = self.config["states"].get(current_state_name)
        if not state_config:
            logger.error(f"Unknown state: {current_state_name}")
            return state, "end"

        state_type = state_config.get("type", "")

        if state_type == "llm_processor":
            return self.process_llm_processor_state(
                state, state_config, agent, authoritative_user_id, token_context
            )
        elif state_type == "intent_classifier":
            return self.process_intent_classifier_state(
                state, state_config, agent, authoritative_user_id, token_context
            )
        elif state_type == "llm_validator":
            return self.process_llm_validator_state(
                state, state_config, agent, authoritative_user_id, token_context
            )
        elif state_type == "terminal":
            return self.process_terminal_state(state, state_config)
        else:
            logger.error(f"Unknown state type: {state_type}")
            return state, "end"


class ConversationSession:
    """
    Encapsulates the state machine, graph, and persistent conversation state for a single conversation session.
    Uses LangGraph checkpoints and threads for persistence across process restarts.
    """

    def __init__(  # type: ignore[no-untyped-def]
        self,
        agent,
        thread_id: str | None = None,
        checkpoint_db_path: str | None = None,
        authoritative_user_id: str | None = None,
    ):
        """
        Initialize a new conversation session with persistent checkpoint storage.

        Args:
            agent: Agent instance to use for this session (config includes state machine path)
            thread_id: Thread identifier for conversation persistence (defaults to generated ID)
            checkpoint_db_path: Path to SQLite checkpoint database (defaults to memory)
            authoritative_user_id: Optional authoritative user ID for the user
        """
        import uuid

        self.thread_id = thread_id or str(uuid.uuid4())
        self.agent = agent
        self.authoritative_user_id = authoritative_user_id

        # Get state machine config path from agent configuration
        lg_config_path = agent.config.get(
            "lg_state_machine_config", "config/lg-prompts/chat-lg-state.yaml"
        )

        # Convert to absolute path using centralized path resolution
        if not Path(lg_config_path).is_absolute():
            try:
                self.config_path = resolve_asset_manager_path(lg_config_path)
            except FileNotFoundError as e:
                logger.error(f"ConversationSession config not found: {e}")
                raise
        else:
            self.config_path = Path(lg_config_path)

        # Initialize checkpoint storage with SqliteSaver
        self.checkpointer_cm = SqliteSaver.from_conn_string(str(checkpoint_db_path))
        self.checkpointer = self.checkpointer_cm.__enter__()

        # Initialize state machine
        self.state_machine = StateMachine(self.config_path)

        # Create the graph with checkpointer
        self.app = self._create_graph()

        # Thread configuration for this session
        self.thread_config = {"configurable": {"thread_id": self.thread_id}}

        # Store current token context for this session
        self.current_token_context = None

    def _create_graph(self) -> Any:
        """Create the LangGraph workflow with one node per YAML state."""
        # Use the dynamic AgentState from the state machine
        workflow = StateGraph(self.state_machine.AgentState)

        # Get all states from configuration
        states_config = self.state_machine.config.get("states", {})
        settings = self.state_machine.config.get("settings", {})
        initial_state = settings.get("initial_state", "collect_employee_id")

        # Add a node for each state in the YAML configuration
        node_names = []
        for state_name, state_config in states_config.items():
            state_type = state_config.get("type", "")
            node_names.append(state_name)

            # Create node function with closure to capture state_name
            def make_node_func(name, stype):  # type: ignore[no-untyped-def]
                def node_func(state: dict[str, Any]) -> Any:
                    """Node function that returns Command for routing (or state for terminal nodes)."""
                    logger.info(
                        f"Thread {self.thread_id} processing node: {name}, type: {stype}"
                    )

                    # Update current_state to track where we are (for logging/debugging)
                    state["current_state"] = name

                    # Terminal states just return state - explicit edge to END handles routing
                    if stype == "terminal":
                        return state

                    # Waiting states check if there's a new HUMAN message to consume
                    if stype == "waiting":
                        # Get the target from transitions
                        transitions = states_config[name].get("transitions", {})
                        next_node = transitions.get("user_input", "end")

                        # Check if there's a new HumanMessage by counting them
                        messages = state.get("messages", [])
                        human_count = sum(
                            1 for msg in messages if isinstance(msg, HumanMessage)
                        )

                        # Track GLOBALLY which human message number was last processed
                        # Use checkpointed value to persist across invokes
                        last_processed_global = state.get(
                            "_last_processed_human_count", 0
                        )

                        # Also check if we've already consumed a message in THIS invoke
                        # This flag gets set when the FIRST waiting node in an invoke consumes a message
                        consumed_this_invoke = state.get("_consumed_this_invoke", False)

                        if (
                            human_count > last_processed_global
                            and not consumed_this_invoke
                        ):
                            # New human message AND not yet consumed in this invoke - consume it
                            state["_last_processed_human_count"] = human_count
                            state["_consumed_this_invoke"] = (
                                True  # Mark as consumed for this invoke
                            )
                            state["_last_waiting_node"] = (
                                None  # Clear since we're moving on
                            )
                            return Command(goto=next_node, update=state)

                        # Already consumed in this invoke, or no new message - pause execution
                        # Store this waiting node as the resume point
                        state["_last_waiting_node"] = name
                        return state
                    else:
                        # Process the state and get next node
                        updated_state, next_node = self.state_machine.process_state(
                            state,
                            self.agent,
                            self.authoritative_user_id,
                            self.current_token_context,
                        )
                        # Return Command with routing information
                        return Command(goto=next_node, update=updated_state)

                return node_func

            workflow.add_node(state_name, make_node_func(state_name, state_type))

        # Add a resume dispatcher node that routes to the correct starting point
        def resume_dispatcher(state: dict[str, Any]) -> Any:
            """Dispatcher that resumes from last waiting node or starts from initial state"""
            last_waiting_node = state.get("_last_waiting_node")

            if last_waiting_node and last_waiting_node in node_names:
                # Resume from last waiting node
                return Command(goto=last_waiting_node, update=state)
            else:
                # New conversation - start from initial state
                return Command(goto=initial_state, update=state)

        workflow.add_node("__resume_dispatcher__", resume_dispatcher)  # type: ignore[type-var]

        logger.info(f"Created {len(node_names)} nodes: {', '.join(node_names)}")

        # Set entry point to resume dispatcher
        workflow.set_entry_point("__resume_dispatcher__")

        # No need for explicit edges - nodes return Command(goto=X) which handles routing
        # The only explicit edge needed is for terminal states
        for state_name, state_config in states_config.items():
            state_type = state_config.get("type", "")
            if state_type == "terminal":
                # Terminal states always go to END
                workflow.add_edge(state_name, END)

        # Compile with checkpointer only
        return workflow.compile(checkpointer=self.checkpointer, debug=False)

    def get_initial_response(self) -> str:
        """Get the initial response from the agent by checking conversation history."""
        try:
            # Check if there's existing conversation state
            current_state = self.app.get_state(self.thread_config)

            if current_state.values and current_state.values.get("messages"):
                return ""
            else:
                # New conversation - initialize and get first response
                initial_state = self.state_machine.create_initial_state()
                result = self.app.invoke(initial_state, config=self.thread_config)

                if result.get("messages"):
                    last_message = result["messages"][-1]
                    if isinstance(last_message, AIMessage):
                        return last_message.content
                return ""
        except Exception as e:
            logger.error(
                f"Error getting initial response for thread {self.thread_id}: {e}"
            )
            return ""

    def send_message(self, message: str, token_context: Optional[str] = None) -> str:
        """
        Send a message to the agent and return the response.
        Uses checkpointed thread state for persistence across process restarts.

        Args:
            message: The user message to send to the agent
            token_context: Optional context for token counting (e.g., "session_123")

        Returns:
            The agent's response message as a string
        """

        # Handle special commands
        if message.lower() in ["quit", "exit", "q"]:
            return "Session ended."

        if message.lower() == "**tokens**":
            try:
                # Try to import token stats, but handle gracefully if not available
                try:
                    from asset_manager.token_counter import get_token_stats

                    stats = get_token_stats()
                    return f"CURRENT_TOKEN_SUMMARY:INPUT:{stats.total_input_tokens}:OUTPUT:{stats.total_output_tokens}:TOTAL:{stats.total_tokens}:CALLS:{stats.call_count}:MAX_SINGLE_INPUT:{stats.max_input_tokens}:MAX_SINGLE_OUTPUT:{stats.max_output_tokens}:MAX_SINGLE_TOTAL:{stats.max_total_tokens}"
                except ImportError:
                    return "Token stats not available"
            except Exception:
                return "Token stats not available"

        if not message.strip():
            return "Please provide a valid message."

        try:
            # Get current thread state
            current_state = self.app.get_state(self.thread_config)

            # Initialize conversation if needed
            if not current_state.values:
                # First message - initialize with empty state and let the graph handle initialization
                initial_state = self.state_machine.create_initial_state()

                # Check for initial_user_message override in settings
                settings = self.state_machine.config.get("settings", {})
                initial_user_message = settings.get("initial_user_message")

                # Use override message if configured, otherwise use the passed message
                message_to_use = (
                    initial_user_message if initial_user_message else message
                )

                # Add the user message to initial state
                initial_state["messages"].append(HumanMessage(content=message_to_use))

                # Only mark as consumed if initial_state is NOT a waiting state
                # Waiting states need to consume the first message themselves
                initial_state_name = settings.get("initial_state", "")
                if not self.state_machine.is_waiting_state(initial_state_name):
                    # Mark this kickoff message as already processed so waiting nodes don't consume it
                    initial_state["_last_processed_human_count"] = 1

                # Store token_context for access during processing
                if token_context:
                    self.current_token_context = token_context

                result = self.app.invoke(initial_state, config=self.thread_config)
            else:
                # Existing conversation - add user message and continue
                # Add the user message and reset the consumed flag for this new invoke
                user_input = {
                    "messages": [HumanMessage(content=message)],
                    "_consumed_this_invoke": False,  # Reset flag so first waiting node can consume
                }

                # Store token_context for access during processing
                if token_context:
                    self.current_token_context = token_context

                result = self.app.invoke(user_input, config=self.thread_config)

            # Extract agent response
            agent_response = ""
            if result and result.get("messages"):
                # Find the last AI message
                for msg in reversed(result["messages"]):
                    if isinstance(msg, AIMessage):
                        agent_response = msg.content
                        break

            # Check if conversation ended and reset if needed
            if result and self.state_machine.is_terminal_state(
                result.get("current_state")
            ):
                # Check if the end state has reset_behavior configured
                end_state_config = self.state_machine.config.get("states", {}).get(
                    "end", {}
                )
                reset_behavior = end_state_config.get("reset_behavior")

                if reset_behavior:
                    # Reset behavior is configured - reset the conversation
                    reset_state = self.state_machine.reset_state_for_new_conversation()

                    # Don't include current_state in the update - it will cause validation errors
                    # Remove it and let the graph naturally go back to the entry point
                    reset_state_without_current = {
                        k: v for k, v in reset_state.items() if k != "current_state"
                    }

                    # Update the state to clear the data (but don't set current_state)
                    if reset_state_without_current:
                        self.app.update_state(
                            self.thread_config, reset_state_without_current
                        )

                    if agent_response:
                        agent_response += (
                            "\n\n[Conversation completed! Starting new conversation...]"
                        )
                    else:
                        agent_response = (
                            "Conversation completed! Starting new conversation..."
                        )
                else:
                    # No reset behavior - just return the response as-is
                    pass

            return (
                agent_response if agent_response else "No response received from agent"
            )

        except Exception as e:
            logger.error(f"Error processing message for thread {self.thread_id}: {e}")
            return f"Error processing message: {e}"

    def close(self) -> None:
        """Clean up resources, especially the SQLite context manager."""
        if hasattr(self, "checkpointer_cm") and self.checkpointer_cm is not None:
            try:
                self.checkpointer_cm.__exit__(None, None, None)
            except Exception as e:
                logger.warning(f"Error closing checkpointer context manager: {e}")

    def __del__(self):  # type: ignore[no-untyped-def]
        """Destructor to ensure cleanup."""
        self.close()
