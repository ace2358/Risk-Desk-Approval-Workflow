from abc import ABC, abstractmethod
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from workflow_engine.schemas import Identifier, WorkflowState


class ToolUnavailable(Exception):
    pass


class AgentContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    workflow_instance_id: int = Field(ge=1)
    actor_id: Identifier


class WorkflowTools:
    def __init__(
        self,
        workflow_reader: Callable[[int], WorkflowState],
        application_reader: Callable[[str, str], dict[str, Any]] | None = None,
        roles_reader: Callable[[], list[str]] | None = None,
    ):
        self._workflow_reader = workflow_reader
        self._application_reader = application_reader
        self._roles_reader = roles_reader

    def get_workflow_state(self, instance_id: int) -> WorkflowState:
        return self._workflow_reader(instance_id).model_copy(deep=True)

    def get_application(self, entity_type: str, entity_id: str) -> dict[str, Any]:
        if self._application_reader is None:
            raise ToolUnavailable("No application provider is configured")
        return deepcopy(self._application_reader(entity_type, entity_id))

    def get_available_roles(self) -> list[str]:
        if self._roles_reader is None:
            raise ToolUnavailable("No role provider is configured")
        return list(self._roles_reader())

    def add_approval(self, instance_id: int, role: str) -> None:
        raise ToolUnavailable("Adding reviewers requires a future deterministic engine operation")

    def request_information(self, instance_id: int, message: str) -> None:
        raise ToolUnavailable("Information requests are not implemented in V0")

    def escalate_workflow(self, instance_id: int, reason: str) -> None:
        raise ToolUnavailable("Escalation is not implemented in V0")


class WorkflowAgent(ABC):
    def __init__(self, tools: WorkflowTools):
        self.tools = tools

    @abstractmethod
    def run(self, context: AgentContext) -> None:
        """Perform reasoning through tools; no agent implementation is enabled in V0."""