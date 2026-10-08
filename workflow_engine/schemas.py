from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from workflow_engine.models import StepStatus, WorkflowStatus


Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActorRequest(RequestModel):
    user_id: Identifier


class CreateWorkflowRequest(ActorRequest):
    entity_type: Identifier
    entity_id: Identifier
    assignments: dict[Identifier, Identifier]


class RejectionRequest(ActorRequest):
    reason: Identifier


class SendBackRequest(RejectionRequest):
    target_step_id: int = Field(ge=1)


class ForwardApprovalRequest(RejectionRequest):
    to_user_id: Identifier


class MockUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    role: str


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