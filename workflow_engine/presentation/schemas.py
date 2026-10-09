from pydantic import BaseModel, ConfigDict, Field

from workflow_engine.application.contracts import EventState, Identifier, ORMResponse, StepState, WorkflowState


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActorRequest(RequestModel):
    user_id: Identifier


class CreateFromDefinitionRequest(ActorRequest):
    entity_type: Identifier
    entity_id: Identifier


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


__all__ = [
    "Identifier", "RequestModel", "ActorRequest", "CreateWorkflowRequest", "RejectionRequest",
    "SendBackRequest", "ForwardApprovalRequest", "MockUser", "ORMResponse", "StepState", "WorkflowState", "EventState",
]