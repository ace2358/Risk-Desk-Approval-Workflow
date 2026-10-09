from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from workflow_engine.domain.types import StepStatus, WorkflowStatus


Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class CreateWorkflowCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: Identifier
    entity_type: Identifier
    entity_id: Identifier
    assignments: dict[Identifier, Identifier]


class ORMResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class StepState(ORMResponse):
    id: int
    workflow_instance_id: int
    name: str
    step_type: str
    status: StepStatus
    order: int
    assigned_to: str
    original_assigned_to: str


class WorkflowState(ORMResponse):
    id: int
    workflow_definition_id: int
    entity_type: str
    entity_id: str
    status: WorkflowStatus
    created_at: datetime
    completed_at: datetime | None
    steps: list[StepState]


class EventState(ORMResponse):
    id: int
    workflow_instance_id: int
    step_id: int | None
    event_type: str
    actor_id: str
    created_at: datetime
    metadata: dict[str, Any] = Field(validation_alias="event_metadata")