from datetime import datetime, timedelta
from enum import Enum

import jinja2
import mqtt_ingest_pipeline.iot_data_transformer as models
from private_assistant_commons import messages, skill_config
from private_assistant_commons.base_skill import BaseSkill
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession


class DeviceType(Enum):
    WINDOW = "window_sensor"


class Action(Enum):
    STATE_QUERY = ["state_query"]


class StateFilter(Enum):
    ALL = "all"
    OPEN = "open"
    CLOSED = "closed"


class Parameters(BaseModel):
    action: Action = Action.STATE_QUERY
    device_type: DeviceType
    rooms: list[str] = []
    state_filter: StateFilter = StateFilter.ALL
    states: list[tuple] = []


class IoTStateSkill(BaseSkill):
    def __init__(
        self,
        config_obj: skill_config.SkillConfig,
        template_env: jinja2.Environment,
        db_engine: AsyncEngine,
        **kwargs,
    ) -> None:
        super().__init__(config_obj=config_obj, **kwargs)
        self.db_engine = db_engine
        self.template_env = template_env
        self.action_to_template: dict[Action, jinja2.Template] = {}

        self.device_type_map: dict[str, DeviceType] = {
            "window": DeviceType.WINDOW,
            "windows": DeviceType.WINDOW,
        }

    def _load_templates(self) -> None:
        try:
            for action in Action:
                self.action_to_template[action] = self.template_env.get_template(f"{action.name.lower()}.j2")
            self.logger.debug("Templates loaded successfully")
        except jinja2.TemplateNotFound as e:
            self.logger.error("Failed to load template: %s", e)

    async def skill_preparations(self) -> None:
        self._load_templates()

    async def calculate_certainty(self, intent_analysis_result: messages.IntentAnalysisResult) -> float:
        return 1.0 if any(noun in self.device_type_map for noun in intent_analysis_result.nouns) else 0.0

    def get_parameters(self, intent_analysis_result: messages.IntentAnalysisResult) -> Parameters:
        device_type = next(
            (self.device_type_map[noun] for noun in intent_analysis_result.nouns if noun in self.device_type_map), None
        )
        if not device_type:
            raise ValueError("No valid device type found in request")

        rooms = intent_analysis_result.rooms or [intent_analysis_result.client_request.room]

        # Determine state filter from text
        text_words = set(intent_analysis_result.client_request.text.lower().split())
        state_filter = (
            StateFilter.OPEN
            if "open" in text_words
            else StateFilter.CLOSED
            if "closed" in text_words
            else StateFilter.ALL
        )

        return Parameters(device_type=device_type, rooms=rooms, state_filter=state_filter)

    async def get_device_states(self, params: Parameters) -> list[tuple]:
        rooms_wo_whitespace = [room.replace(" ", "") for room in params.rooms]
        async with AsyncSession(self.db_engine) as session:
            subquery = (
                select(
                    models.IoTData,
                    func.row_number()
                    .over(partition_by=models.IoTData.device_id, order_by=col(models.IoTData.time).desc())
                    .label("row_num"),
                )
                .where(models.IoTData.device_type == params.device_type.value)
                .where(models.IoTData.time > datetime.now() - timedelta(days=2))
                .where(col(models.IoTData.room).in_(rooms_wo_whitespace))
            ).subquery()

            if params.state_filter != StateFilter.ALL:
                is_closed = params.state_filter != StateFilter.OPEN
                filter_query = select(subquery.c.device_name, subquery.c.room).where(
                    subquery.c.payload["contact"].astext == str(is_closed).lower()
                )
            query = filter_query.where(subquery.c.row_num == 1)
            result = await session.exec(query)
            return list(result.all())

    def get_answer(self, params: Parameters) -> str:
        template = self.action_to_template.get(params.action)
        if template:
            return template.render(params=params)
        self.logger.error("No template found for action %s", params.action)
        return "Sorry, I couldn't process your request"

    async def process_request(self, intent: messages.IntentAnalysisResult) -> None:
        params = self.get_parameters(intent)
        params.states = await self.get_device_states(params)
        response = self.get_answer(params)
        await self.send_response(response, intent.client_request)

    async def cleanup(self) -> None:
        await self.db_engine.dispose()
