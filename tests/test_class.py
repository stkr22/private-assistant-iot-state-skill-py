import uuid
from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

import jinja2
import pytest
from private_assistant_commons import messages
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import StaticPool

from private_assistant_iot_state_skill.config import SkillConfig
from private_assistant_iot_state_skill.iot_state_skill import Action, DeviceType, IoTStateSkill, Parameters, StateFilter


@pytest.fixture
def template_env() -> jinja2.Environment:
    env = jinja2.Environment(
        loader=jinja2.DictLoader({"state_query.j2": "Test template for {{ params.device_type.value }}"})
    )
    return env


@pytest.fixture
async def db_engine() -> AsyncGenerator[AsyncEngine, None]:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    yield engine
    await engine.dispose()


@pytest.fixture
def config() -> SkillConfig:
    return SkillConfig(
        client_id="test_skill",
        mqtt_server_host="localhost",
        mqtt_server_port=1883,
        iot_postgres_user="test_user",
        iot_postgres_db="test_db",
        iot_postgres_host="localhost",
        iot_postgres_port=5432,
    )


@pytest.fixture
def sample_intent() -> messages.IntentAnalysisResult:
    return messages.IntentAnalysisResult(
        id=uuid.UUID("90f54af1-77d9-4074-b0e8-2a99119116be"),
        client_request=messages.ClientRequest(
            id=uuid.UUID("bdfc40a6-4284-42ce-b880-c1898f0d78d1"),
            text="Please tell me about closed windows.",
            room="living room",
            output_topic="assistant/living room/output",
        ),
        rooms=[],
        numbers=[],
        verbs=["tell"],
        nouns=["windows"],
    )


@pytest.fixture
async def skill(template_env: jinja2.Environment, db_engine: AsyncEngine, config: SkillConfig) -> IoTStateSkill:
    mqtt_client = MagicMock()
    task_group = MagicMock()
    skill = IoTStateSkill(
        config_obj=config,
        template_env=template_env,
        db_engine=db_engine,
        mqtt_client=mqtt_client,
        task_group=task_group,
    )
    await skill.skill_preparations()
    return skill


@pytest.mark.asyncio
async def test_calculate_certainty(skill: IoTStateSkill, sample_intent: messages.IntentAnalysisResult) -> None:
    certainty = await skill.calculate_certainty(sample_intent)
    assert certainty == 1.0

    sample_intent.nouns = ["invalid"]
    certainty = await skill.calculate_certainty(sample_intent)
    assert certainty == 0.0


@pytest.mark.asyncio
async def test_get_parameters(skill: IoTStateSkill, sample_intent: messages.IntentAnalysisResult) -> None:
    params = skill.get_parameters(sample_intent)
    assert params.device_type == DeviceType.WINDOW
    assert params.rooms == ["living room"]
    assert params.state_filter == StateFilter.CLOSED

    # Test with open windows
    sample_intent.client_request.text = "Are there any open windows?"
    params = skill.get_parameters(sample_intent)
    assert params.state_filter == StateFilter.OPEN

    # Test with invalid device type
    sample_intent.nouns = ["invalid"]
    with pytest.raises(ValueError):
        skill.get_parameters(sample_intent)


@pytest.mark.asyncio
async def test_get_answer(skill: IoTStateSkill) -> None:
    params = Parameters(
        action=Action.STATE_QUERY,
        device_type=DeviceType.WINDOW,
        rooms=["living room"],
        state_filter=StateFilter.CLOSED,
        states=[("window1", "living room")],
    )
    answer = skill.get_answer(params)
    assert "Test template for window_sensor" in answer
