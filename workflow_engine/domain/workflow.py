from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from workflow_engine.domain.types import (
    InvalidInput, InvalidTransition, NotFound, PermissionDenied, StepStatus, WorkflowStatus, utc_now,
)


@dataclass
class ApprovalAssignment:
    current: str
    original: str


@dataclass
class StepExecution:
    id: int
    workflow_instance_id: int
    name: str
    step_type: str
    status: StepStatus
    order: int
    assignment: ApprovalAssignment

    @property
    def assigned_to(self) -> str:
        return self.assignment.current

    @property
    def original_assigned_to(self) -> str:
        return self.assignment.original


@dataclass(frozen=True)
class Transition:
    from_step: str
    to_step: str
    condition: str = "always"


@dataclass(frozen=True)
class WorkflowEvent:
    workflow_instance_id: int
    step_id: int | None
    event_type: str
    actor_id: str
    event_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    id: int | None = None


@dataclass
class WorkflowExecution:
    id: int
    workflow_definition_id: int
    entity_type: str
    entity_id: str
    status: WorkflowStatus
    created_at: datetime
    completed_at: datetime | None
    steps: list[StepExecution]
    transitions: tuple[Transition, ...]
    submitter: str | None
    pending_events: list[WorkflowEvent] = field(default_factory=list, repr=False)

    def record(self, event_type: str, actor: str, step: StepExecution | None = None,
               metadata: dict[str, Any] | None = None) -> None:
        self.pending_events.append(WorkflowEvent(
            self.id, step.id if step else None, event_type, actor, metadata or {},
        ))

    def record_creation(self) -> None:
        self.record("workflow_created", self.submitter, metadata={
            "assignments": [{"step_id": step.id, "assigned_to": step.assigned_to} for step in self.steps],
        })

    def require_status(self, status: WorkflowStatus) -> None:
        if self.status != status:
            raise InvalidTransition(f"Workflow must be {status}, not {self.status}")

    def require_owner(self, actor: str) -> None:
        if not actor or self.submitter != actor:
            raise PermissionDenied("Only the submitter can start or cancel this workflow")

    def step(self, step_id: int, *, target: bool = False) -> StepExecution:
        step = next((step for step in self.steps if step.id == step_id), None)
        if step is None:
            raise NotFound("Target step does not exist in this workflow" if target else "Step does not exist in this workflow")
        return step

    def actionable_step(self, step_id: int, actor: str) -> StepExecution:
        step = self.step(step_id)
        self.require_status(WorkflowStatus.RUNNING)
        if step.status != StepStatus.ACTIVE:
            raise InvalidTransition("Only an active step can be actioned")
        if not actor or step.assigned_to != actor:
            raise PermissionDenied("Only the assigned reviewer can act on this step")
        return step

    def start(self, actor: str) -> None:
        self.require_status(WorkflowStatus.PENDING)
        self.require_owner(actor)
        if not self.steps or any(step.status != StepStatus.PENDING for step in self.steps):
            raise InvalidTransition("A new workflow must have only pending steps")
        self.status = WorkflowStatus.RUNNING
        self.steps[0].status = StepStatus.ACTIVE
        self.record("workflow_started", actor)
        self.record("step_activated", actor, self.steps[0])

    def approve(self, step_id: int, actor: str) -> None:
        step = self.actionable_step(step_id, actor)
        transition = next((item for item in self.transitions if item.from_step == step.name), None)
        next_step = None
        if transition is not None:
            if transition.condition != "always":
                raise InvalidTransition("Only unconditional transitions are supported")
            next_step = next((item for item in self.steps if item.name == transition.to_step), None)
            if next_step is None or next_step.status != StepStatus.PENDING:
                raise InvalidTransition("Transition target must be a pending step")
        elif any(item.id != step.id and item.status != StepStatus.APPROVED for item in self.steps):
            raise InvalidTransition("Workflow has unfinished steps but no next transition")
        step.status = StepStatus.APPROVED
        self.record("step_approved", actor, step)
        if next_step is not None:
            next_step.status = StepStatus.ACTIVE
            self.record("step_activated", actor, next_step)
        else:
            self.status = WorkflowStatus.COMPLETED
            self.completed_at = utc_now()
            self.record("workflow_completed", actor)

    def send_back(self, step_id: int, target_step_id: int, actor: str, reason: str) -> None:
        reason = require_reason(reason)
        current = self.actionable_step(step_id, actor)
        target = self.step(target_step_id, target=True)
        if target.order >= current.order or target.status != StepStatus.APPROVED:
            raise InvalidTransition("Send-back target must be an approved previous step")
        path = [step for step in self.steps if target.order <= step.order <= current.order]
        transitions = {transition.from_step: transition for transition in self.transitions}
        for previous, following in zip(path, path[1:]):
            transition = transitions.get(previous.name)
            if (transition is None or transition.condition != "always"
                    or transition.to_step != following.name or previous.status != StepStatus.APPROVED):
                raise InvalidTransition("Target must reach the active step through approved sequential steps")
        reset_steps = [step for step in self.steps if step.order >= target.order]
        changes = [{"step_id": step.id, "previous_status": step.status.value,
                    "status": StepStatus.PENDING.value} for step in reset_steps]
        for step in reset_steps:
            step.status = StepStatus.PENDING
        self.record("step_sent_back", actor, current, {
            "target_step_id": target.id, "reason": reason, "reset_steps": changes,
        })
        target.status = StepStatus.ACTIVE
        self.record("step_activated", actor, target)

    def forward(self, step_id: int, actor: str, recipient: str, reason: str,
                recipient_is_eligible: Callable[[str], bool] | None) -> None:
        reason = require_reason(reason)
        step = self.actionable_step(step_id, actor)
        recipient = recipient.strip()
        if not recipient or recipient == actor:
            raise InvalidInput("Recipient must be a different nonblank user")
        if recipient_is_eligible is None or not recipient_is_eligible(recipient):
            raise InvalidInput("Recipient is not eligible to receive this approval")
        step.assignment.current = recipient
        self.record("approval_forwarded", actor, step, {
            "from_user_id": actor, "to_user_id": recipient,
            "original_assigned_to": step.original_assigned_to, "reason": reason,
        })

    def reject(self, step_id: int, actor: str, reason: str) -> None:
        if not reason.strip():
            raise InvalidInput("Rejection requires a reason")
        step = self.actionable_step(step_id, actor)
        step.status = StepStatus.REJECTED
        self.record("step_rejected", actor, step, {"reason": reason.strip()})
        self.skip_unfinished(actor)
        self.status = WorkflowStatus.REJECTED
        self.completed_at = utc_now()
        self.record("workflow_rejected", actor)

    def cancel(self, actor: str) -> None:
        if self.status not in (WorkflowStatus.PENDING, WorkflowStatus.RUNNING):
            raise InvalidTransition("Only pending or running workflows can be cancelled")
        self.require_owner(actor)
        self.skip_unfinished(actor)
        self.status = WorkflowStatus.CANCELLED
        self.completed_at = utc_now()
        self.record("workflow_cancelled", actor)

    def skip_unfinished(self, actor: str) -> None:
        for step in self.steps:
            if step.status in (StepStatus.PENDING, StepStatus.ACTIVE):
                step.status = StepStatus.SKIPPED
                self.record("step_skipped", actor, step)


def require_reason(reason: str) -> str:
    if not reason.strip():
        raise InvalidInput("A nonblank reason is required")
    return reason.strip()