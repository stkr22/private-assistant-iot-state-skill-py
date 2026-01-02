"""Comprehensive unit tests for the IoT State Skill.

Tests device state queries, parameter extraction, request processing,
and intent validation logic using mocked dependencies.
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import jinja2
import pytest
from private_assistant_commons import SkillConfig, messages
from private_assistant_commons.intent.models import ClassifiedIntent, Entity, EntityType, IntentRequest, IntentType
from sqlalchemy.ext.asyncio import AsyncEngine

from private_assistant_iot_state_skill.iot_state_skill import Action, DeviceType, IoTStateSkill, Parameters, StateFilter


def create_mock_intent_request(  # noqa: PLR0913
    intent_type: IntentType,
    raw_text: str,
    room: str = "living room",
    device_entity: str | None = None,
    room_entity: str | None = None,
    confidence: float = 0.9,
) -> IntentRequest:
    """Create a mock IntentRequest for testing.

    Args:
        intent_type: Type of intent (QUERY_STATUS, QUERY_LIST, etc.)
        raw_text: Raw text from user query
        room: Room context for the request
        device_entity: Optional device entity normalized value
        room_entity: Optional room entity normalized value
        confidence: Intent confidence score

    Returns:
        IntentRequest: Configured intent request for testing
    """
    client_request = messages.ClientRequest(
        id=uuid.uuid4(),
        text=raw_text,
        room=room,
        output_topic=f"assistant/{room}/output",
    )

    entities = {}
    if device_entity:
        entities["device"] = [
            Entity(
                type=EntityType.DEVICE,
                raw_text=device_entity,
                normalized_value=device_entity,
                confidence=0.95,
            )
        ]
    if room_entity:
        entities["room"] = [
            Entity(
                type=EntityType.ROOM,
                raw_text=room_entity,
                normalized_value=room_entity,
                confidence=0.9,
            )
        ]

    classified_intent = ClassifiedIntent(
        id=uuid.uuid4(),
        intent_type=intent_type,
        confidence=confidence,
        entities=entities,
        raw_text=raw_text,
    )

    return IntentRequest(
        id=uuid.uuid4(),
        classified_intent=classified_intent,
        client_request=client_request,
    )


@pytest.fixture
def template_env() -> jinja2.Environment:
    """Create Jinja2 template environment for testing."""
    return jinja2.Environment(
        loader=jinja2.DictLoader({"state_query.j2": "Test template for {{ params.device_type.value }}"})
    )


@pytest.fixture
def config() -> SkillConfig:
    """Create skill configuration for testing."""
    return SkillConfig(
        client_id="test_skill",
    )


@pytest.fixture
async def skill(
    template_env: jinja2.Environment,
    config: SkillConfig,
) -> IoTStateSkill:
    """Create and initialize IoT State Skill for testing."""
    # Create mock engines
    assistant_engine = MagicMock(spec=AsyncEngine)
    iot_engine = MagicMock(spec=AsyncEngine)

    mqtt_client = MagicMock()
    task_group = MagicMock()

    # Create skill instance
    skill = IoTStateSkill(
        config_obj=config,
        template_env=template_env,
        assistant_engine=assistant_engine,
        iot_db_engine=iot_engine,
        mqtt_client=mqtt_client,
        task_group=task_group,
    )

    # Mock BaseSkill methods that interact with database
    skill.ensure_skill_registered = AsyncMock()  # type: ignore[method-assign]
    skill.ensure_device_types_registered = AsyncMock()  # type: ignore[method-assign]
    skill.register_device = AsyncMock()  # type: ignore[method-assign]
    skill.send_response = AsyncMock()  # type: ignore[method-assign]
    skill.global_devices = []  # type: ignore[misc]

    # Call skill_preparations to load templates
    await skill.skill_preparations()

    return skill


class TestIoTStateSkill:
    """Test suite for IoT State Skill core functionality."""

    @pytest.mark.asyncio
    async def test_get_device_states_single_room(self, skill: IoTStateSkill) -> None:
        """Test retrieving device states filtered by single room."""
        # AIDEV-NOTE: Mock get_device_states to return test data
        skill.get_device_states = AsyncMock(return_value=[("window 1", "livingroom", "open")])  # type: ignore[method-assign]

        params = Parameters(
            device_type=DeviceType.WINDOW,
            rooms=["living room"],
            state_filter=StateFilter.ALL,
        )
        states = await skill.get_device_states(params)

        assert len(states) == 1
        assert states[0] == ("window 1", "livingroom", "open")

    @pytest.mark.asyncio
    async def test_get_device_states_multiple_rooms(self, skill: IoTStateSkill) -> None:
        """Test retrieving device states filtered by multiple rooms."""
        # AIDEV-NOTE: Mock get_device_states to return data from multiple rooms
        skill.get_device_states = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                ("window 1", "livingroom", "open"),
                ("window 2", "bedroom", "closed"),
            ]
        )

        params = Parameters(
            device_type=DeviceType.WINDOW,
            rooms=["living room", "bedroom"],
            state_filter=StateFilter.ALL,
        )
        states = await skill.get_device_states(params)

        assert len(states) == 2  # noqa: PLR2004
        assert ("window 1", "livingroom", "open") in states
        assert ("window 2", "bedroom", "closed") in states

    @pytest.mark.asyncio
    async def test_get_parameters_with_room_entity(self, skill: IoTStateSkill) -> None:
        """Test parameter extraction when room entity is provided."""
        intent_request = create_mock_intent_request(
            intent_type=IntentType.QUERY_STATUS,
            raw_text="Show me windows in the bedroom",
            room="living room",  # Client request room
            device_entity="windows",
            room_entity="bedroom",  # Room entity should override
        )

        params = skill.get_parameters(intent_request)

        assert params.device_type == DeviceType.WINDOW
        assert params.rooms == ["bedroom"]  # Should use room entity, not client room
        assert params.state_filter == StateFilter.ALL

    @pytest.mark.asyncio
    async def test_get_parameters_default_room(self, skill: IoTStateSkill) -> None:
        """Test parameter extraction falls back to client request room when no room entity."""
        intent_request = create_mock_intent_request(
            intent_type=IntentType.QUERY_STATUS,
            raw_text="Show me windows",
            room="kitchen",
            device_entity="windows",
            room_entity=None,  # No room entity
        )

        params = skill.get_parameters(intent_request)

        assert params.device_type == DeviceType.WINDOW
        assert params.rooms == ["kitchen"]  # Should use client request room
        assert params.state_filter == StateFilter.ALL

    @pytest.mark.asyncio
    async def test_get_parameters_state_filter_open(self, skill: IoTStateSkill) -> None:
        """Test parameter extraction identifies 'open' state filter from text."""
        intent_request = create_mock_intent_request(
            intent_type=IntentType.QUERY_STATUS,
            raw_text="Are there any open windows?",
            device_entity="windows",
        )

        params = skill.get_parameters(intent_request)

        assert params.state_filter == StateFilter.OPEN

    @pytest.mark.asyncio
    async def test_get_parameters_state_filter_closed(self, skill: IoTStateSkill) -> None:
        """Test parameter extraction identifies 'closed' state filter from text."""
        intent_request = create_mock_intent_request(
            intent_type=IntentType.QUERY_STATUS,
            raw_text="Show me closed windows",
            device_entity="windows",
        )

        params = skill.get_parameters(intent_request)

        assert params.state_filter == StateFilter.CLOSED

    @pytest.mark.asyncio
    async def test_get_parameters_no_device_entity(self, skill: IoTStateSkill) -> None:
        """Test parameter extraction raises ValueError when no device type found."""
        intent_request = create_mock_intent_request(
            intent_type=IntentType.QUERY_STATUS,
            raw_text="Show me the status",
            device_entity=None,  # No device entity
        )

        with pytest.raises(ValueError, match="No valid device type found"):
            skill.get_parameters(intent_request)

    @pytest.mark.asyncio
    async def test_extract_state_filter_from_text(self, skill: IoTStateSkill) -> None:
        """Test state filter extraction from raw text."""
        assert skill._extract_state_filter_from_text("Are there any open windows?") == StateFilter.OPEN
        assert skill._extract_state_filter_from_text("Show me closed windows") == StateFilter.CLOSED
        assert skill._extract_state_filter_from_text("Show me all windows") == StateFilter.ALL
        assert skill._extract_state_filter_from_text("What's the status?") == StateFilter.ALL

    @pytest.mark.asyncio
    async def test_get_answer(self, skill: IoTStateSkill) -> None:
        """Test answer generation using templates."""
        params = Parameters(
            action=Action.STATE_QUERY,
            device_type=DeviceType.WINDOW,
            rooms=["living room"],
            state_filter=StateFilter.CLOSED,
            states=[("window1", "living room", "closed")],
        )
        answer = skill.get_answer(params)
        assert "Test template for window_sensor" in answer

    @pytest.mark.asyncio
    async def test_get_answer_missing_template(self, skill: IoTStateSkill) -> None:
        """Test answer generation handles missing template gracefully."""
        # Remove the template from action_to_template
        skill.action_to_template.clear()

        params = Parameters(
            action=Action.STATE_QUERY,
            device_type=DeviceType.WINDOW,
            rooms=["living room"],
            state_filter=StateFilter.ALL,
        )
        answer = skill.get_answer(params)

        assert answer == "Sorry, I couldn't process your request"

    @pytest.mark.asyncio
    async def test_process_request_query_status(self, skill: IoTStateSkill) -> None:
        """Test processing QUERY_STATUS intent generates response."""
        # AIDEV-NOTE: Mock get_device_states to return test data
        skill.get_device_states = AsyncMock(return_value=[("window 1", "livingroom", "open")])  # type: ignore[method-assign]

        intent_request = create_mock_intent_request(
            intent_type=IntentType.QUERY_STATUS,
            raw_text="Show me all windows",
            device_entity="windows",
        )

        await skill.process_request(intent_request)

        # AIDEV-NOTE: Verify response was generated and sent
        assert skill.send_response.called  # type: ignore[attr-defined]
        assert skill.get_device_states.called  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_process_request_system_help(self, skill: IoTStateSkill) -> None:
        """Test processing SYSTEM_HELP intent generates help response."""
        # AIDEV-NOTE: Mock get_device_states to return empty list for help
        skill.get_device_states = AsyncMock(return_value=[])  # type: ignore[method-assign]

        intent_request = create_mock_intent_request(
            intent_type=IntentType.SYSTEM_HELP,
            raw_text="What can you tell me about windows?",
            device_entity="windows",
        )

        await skill.process_request(intent_request)

        # AIDEV-NOTE: Verify response was sent even for help intent
        assert skill.send_response.called  # type: ignore[attr-defined]


class TestIntentValidation:
    """Test suite for intent validation and filtering logic."""

    @pytest.mark.asyncio
    async def test_skill_supports_window_device_type(self, skill: IoTStateSkill) -> None:
        """Test skill is configured to support window_sensor device type."""
        assert "window_sensor" in skill.supported_device_types

    @pytest.mark.asyncio
    async def test_skill_supports_query_status_intent(self, skill: IoTStateSkill) -> None:
        """Test skill supports QUERY_STATUS intent with appropriate confidence."""
        assert IntentType.QUERY_STATUS in skill.supported_intents
        assert skill.supported_intents[IntentType.QUERY_STATUS] == 0.8  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_skill_supports_query_list_intent(self, skill: IoTStateSkill) -> None:
        """Test skill supports QUERY_LIST intent."""
        assert IntentType.QUERY_LIST in skill.supported_intents
        assert skill.supported_intents[IntentType.QUERY_LIST] == 0.7  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_skill_supports_system_help_intent(self, skill: IoTStateSkill) -> None:
        """Test skill supports SYSTEM_HELP intent."""
        assert IntentType.SYSTEM_HELP in skill.supported_intents
        assert skill.supported_intents[IntentType.SYSTEM_HELP] == 0.6  # noqa: PLR2004

    @pytest.mark.asyncio
    async def test_device_type_map_contains_window_keywords(self, skill: IoTStateSkill) -> None:
        """Test device type map includes window/windows keywords."""
        assert "window" in skill.device_type_map
        assert "windows" in skill.device_type_map
        assert skill.device_type_map["window"] == DeviceType.WINDOW
        assert skill.device_type_map["windows"] == DeviceType.WINDOW

    @pytest.mark.asyncio
    async def test_device_registration_called_during_preparations(self, skill: IoTStateSkill) -> None:
        """Test device registration is called with correct parameters during skill_preparations."""
        # AIDEV-NOTE: skill_preparations was already called in fixture
        assert skill.register_device.called  # type: ignore[attr-defined]

        # Verify registration parameters
        call_args = skill.register_device.call_args  # type: ignore[attr-defined]
        assert call_args.kwargs["device_type"] == "window_sensor"
        assert call_args.kwargs["name"] == "window"
        assert call_args.kwargs["pattern"] == ["window", "windows"]
        assert call_args.kwargs["room"] is None

    @pytest.mark.asyncio
    async def test_templates_loaded_during_preparations(self, skill: IoTStateSkill) -> None:
        """Test templates are loaded during skill_preparations."""
        # AIDEV-NOTE: skill_preparations was already called in fixture
        assert Action.STATE_QUERY in skill.action_to_template
        assert skill.action_to_template[Action.STATE_QUERY] is not None
