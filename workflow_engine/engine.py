from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from workflow_engine.definitions import WorkflowDefinition as DefinitionSpec
from workflow_engine.definitions import technical_risk_workflow
from workflow_engine.models import (
    StepStatus, WorkflowDefinition, WorkflowEvent, WorkflowInstance,
    WorkflowStatus, WorkflowStep, WorkflowTransition, utc_now,
)
from workflow_engine.schemas import CreateWorkflowRequest, EventState, StepState, WorkflowState


class WorkflowError(Exception):
    pass


class NotFound(WorkflowError):
    pass


class InvalidTransition(WorkflowError):
    pass


class PermissionDenied(WorkflowError):
    pass


class InvalidInput(WorkflowError):
    pass


class WorkflowEngine:
    def __init__(
        self, sessions: sessionmaker[Session], definition: DefinitionSpec = technical_risk_workflow,
        *, recipient_is_eligible: Callable[[str], bool] | None = None,
    ):
        self.sessions = sessions
        self.definition = definition
        self.recipient_is_eligible = recipient_is_eligible

    @contextmanager
    def _write(self):
        with self.sessions() as session:
            try:
                session.execute(text("BEGIN IMMEDIATE"))
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def create_workflow(self, entity_type: str, entity_id: str, assignments: dict[str, str], user_id: str) -> WorkflowState:
        submission = CreateWorkflowRequest(
            entity_type=entity_type, entity_id=entity_id, assignments=assignments, user_id=user_id,
        )
        expected = {step.name for step in self.definition.steps}
        if set(submission.assignments) != expected:
            raise InvalidInput(f"Assignments must contain exactly: {', '.join(sorted(expected))}")
        with self._write() as session:
            definition = session.scalar(select(WorkflowDefinition).where(
                WorkflowDefinition.name == self.definition.name,
                WorkflowDefinition.version == self.definition.version,
            ))
            if definition is None:
                definition = WorkflowDefinition(name=self.definition.name, version=self.definition.version)
                session.add(definition)
                session.flush()
                for current, following in zip(self.definition.steps, self.definition.steps[1:]):
                    session.add(WorkflowTransition(
                        workflow_definition_id=definition.id, from_step=current.name,
                        to_step=following.name, condition="always",
                    ))
            else:
                stored_transitions = {
                    (transition.from_step, transition.to_step, transition.condition)
                    for transition in session.scalars(select(WorkflowTransition).where(
                        WorkflowTransition.workflow_definition_id == definition.id,
                    ))
                }
                expected_transitions = {
                    (current.name, following.name, "always")
                    for current, following in zip(self.definition.steps, self.definition.steps[1:])
                }
                sample_instance = session.scalar(select(WorkflowInstance).where(
                    WorkflowInstance.workflow_definition_id == definition.id,
                ).order_by(WorkflowInstance.id))
                stored_steps = (
                    [(step.name, step.step_type) for step in self._steps(session, sample_instance.id)]
                    if sample_instance is not None else None
                )
                expected_steps = [(step.name, step.step_type) for step in self.definition.steps]
                if (stored_transitions != expected_transitions
                        or (stored_steps is not None and stored_steps != expected_steps)):
                    raise InvalidInput("Definition steps have changed; increment the workflow version")
            instance = WorkflowInstance(
                workflow_definition_id=definition.id,
                entity_type=submission.entity_type, entity_id=submission.entity_id,
                status=WorkflowStatus.PENDING,
            )
            session.add(instance)
            session.flush()
            for order, step in enumerate(self.definition.steps, start=1):
                session.add(WorkflowStep(
                    workflow_instance_id=instance.id, name=step.name, step_type=step.step_type,
                    status=StepStatus.PENDING, order=order,
                    assigned_to=submission.assignments[step.name],
                    original_assigned_to=submission.assignments[step.name],
                ))
            session.flush()
            self._event(session, instance, "workflow_created", submission.user_id, metadata={
                "assignments": [
                    {"step_id": step.id, "assigned_to": step.assigned_to}
                    for step in self._steps(session, instance.id)
                ],
            })
            return self._state(session, instance)

    def start_workflow(self, instance_id: int, user_id: str) -> WorkflowState:
        with self._write() as session:
            instance = self._instance(session, instance_id)
            self._require_status(instance, WorkflowStatus.PENDING)
            self._require_owner(session, instance, user_id)
            steps = self._steps(session, instance.id)
            if not steps or any(step.status != StepStatus.PENDING for step in steps):
                raise InvalidTransition("A new workflow must have only pending steps")
            instance.status = WorkflowStatus.RUNNING
            steps[0].status = StepStatus.ACTIVE
            self._event(session, instance, "workflow_started", user_id)
            self._event(session, instance, "step_activated", user_id, steps[0])
            return self._state(session, instance)

    def approve_step(self, instance_id: int, step_id: int, user_id: str) -> WorkflowState:
        with self._write() as session:
            instance, step = self._actionable_step(session, instance_id, step_id, user_id)
            step.status = StepStatus.APPROVED
            self._event(session, instance, "step_approved", user_id, step)
            transition = session.scalar(select(WorkflowTransition).where(
                WorkflowTransition.workflow_definition_id == instance.workflow_definition_id,
                WorkflowTransition.from_step == step.name,
            ))
            if transition is not None:
                if transition.condition != "always":
                    raise InvalidTransition("Only unconditional transitions are supported")
                next_step = session.scalar(select(WorkflowStep).where(
                    WorkflowStep.workflow_instance_id == instance.id,
                    WorkflowStep.name == transition.to_step,
                ))
                if next_step is None or next_step.status != StepStatus.PENDING:
                    raise InvalidTransition("Transition target must be a pending step")
                next_step.status = StepStatus.ACTIVE
                self._event(session, instance, "step_activated", user_id, next_step)
            else:
                if any(item.status != StepStatus.APPROVED for item in self._steps(session, instance.id)):
                    raise InvalidTransition("Workflow has unfinished steps but no next transition")
                instance.status = WorkflowStatus.COMPLETED
                instance.completed_at = utc_now()
                self._event(session, instance, "workflow_completed", user_id)
            return self._state(session, instance)

    def send_back_step(
        self, instance_id: int, step_id: int, target_step_id: int, user_id: str, reason: str,
    ) -> WorkflowState:
        reason = self._reason(reason)
        with self._write() as session:
            instance, current = self._actionable_step(session, instance_id, step_id, user_id)
            target = session.get(WorkflowStep, target_step_id)
            if target is None or target.workflow_instance_id != instance.id:
                raise NotFound("Target step does not exist in this workflow")
            if target.order >= current.order or target.status != StepStatus.APPROVED:
                raise InvalidTransition("Send-back target must be an approved previous step")
            steps = self._steps(session, instance.id)
            path = [step for step in steps if target.order <= step.order <= current.order]
            transitions = {
                transition.from_step: transition
                for transition in session.scalars(select(WorkflowTransition).where(
                    WorkflowTransition.workflow_definition_id == instance.workflow_definition_id,
                ))
            }
            for previous, following in zip(path, path[1:]):
                transition = transitions.get(previous.name)
                if (transition is None or transition.condition != "always"
                        or transition.to_step != following.name
                        or previous.status != StepStatus.APPROVED):
                    raise InvalidTransition("Target must reach the active step through approved sequential steps")
            reset_steps = [step for step in steps if step.order >= target.order]
            changes = [
                {"step_id": step.id, "previous_status": step.status.value, "status": StepStatus.PENDING.value}
                for step in reset_steps
            ]
            for step in reset_steps:
                step.status = StepStatus.PENDING
            self._event(session, instance, "step_sent_back", user_id, current, {
                "target_step_id": target.id, "reason": reason, "reset_steps": changes,
            })
            target.status = StepStatus.ACTIVE
            self._event(session, instance, "step_activated", user_id, target)
            return self._state(session, instance)

    def forward_approval(
        self, instance_id: int, step_id: int, from_user_id: str, to_user_id: str, reason: str,
    ) -> WorkflowState:
        reason = self._reason(reason)
        with self._write() as session:
            instance, step = self._actionable_step(session, instance_id, step_id, from_user_id)
            to_user_id = to_user_id.strip()
            if not to_user_id or to_user_id == from_user_id:
                raise InvalidInput("Recipient must be a different nonblank user")
            if self.recipient_is_eligible is None or not self.recipient_is_eligible(to_user_id):
                raise InvalidInput("Recipient is not eligible to receive this approval")
            step.assigned_to = to_user_id
            self._event(session, instance, "approval_forwarded", from_user_id, step, {
                "from_user_id": from_user_id, "to_user_id": to_user_id,
                "original_assigned_to": step.original_assigned_to, "reason": reason,
            })
            return self._state(session, instance)

    def _reason(self, reason: str) -> str:
        if not reason.strip():
            raise InvalidInput("A nonblank reason is required")
        return reason.strip()

    def reject_step(self, instance_id: int, step_id: int, user_id: str, reason: str) -> WorkflowState:
        if not reason.strip():
            raise InvalidInput("Rejection requires a reason")
        with self._write() as session:
            instance, step = self._actionable_step(session, instance_id, step_id, user_id)
            step.status = StepStatus.REJECTED
            self._event(session, instance, "step_rejected", user_id, step, {"reason": reason.strip()})
            self._skip_unfinished(session, instance, user_id)
            instance.status = WorkflowStatus.REJECTED
            instance.completed_at = utc_now()
            self._event(session, instance, "workflow_rejected", user_id)
            return self._state(session, instance)

    def cancel_workflow(self, instance_id: int, user_id: str) -> WorkflowState:
        with self._write() as session:
            instance = self._instance(session, instance_id)
            if instance.status not in (WorkflowStatus.PENDING, WorkflowStatus.RUNNING):
                raise InvalidTransition("Only pending or running workflows can be cancelled")
            self._require_owner(session, instance, user_id)
            self._skip_unfinished(session, instance, user_id)
            instance.status = WorkflowStatus.CANCELLED
            instance.completed_at = utc_now()
            self._event(session, instance, "workflow_cancelled", user_id)
            return self._state(session, instance)

    def list_workflows(self) -> list[WorkflowState]:
        with self.sessions() as session:
            session.execute(text("BEGIN"))
            instances = session.scalars(select(WorkflowInstance).order_by(WorkflowInstance.id.desc())).all()
            return [self._state(session, instance) for instance in instances]

    def get_workflow_state(self, instance_id: int) -> WorkflowState:
        with self.sessions() as session:
            session.execute(text("BEGIN"))
            return self._state(session, self._instance(session, instance_id))

    def get_events(self, instance_id: int) -> list[EventState]:
        with self.sessions() as session:
            session.execute(text("BEGIN"))
            self._instance(session, instance_id)
            events = session.scalars(select(WorkflowEvent).where(
                WorkflowEvent.workflow_instance_id == instance_id,
            ).order_by(WorkflowEvent.id))
            return [EventState.model_validate(event) for event in events]

    def _instance(self, session: Session, instance_id: int) -> WorkflowInstance:
        instance = session.get(WorkflowInstance, instance_id)
        if instance is None:
            raise NotFound("Workflow does not exist")
        return instance

    def _steps(self, session: Session, instance_id: int) -> list[WorkflowStep]:
        return list(session.scalars(select(WorkflowStep).where(
            WorkflowStep.workflow_instance_id == instance_id,
        ).order_by(WorkflowStep.order)))

    def _require_status(self, instance: WorkflowInstance, status: WorkflowStatus) -> None:
        if instance.status != status:
            raise InvalidTransition(f"Workflow must be {status}, not {instance.status}")

    def _require_owner(self, session: Session, instance: WorkflowInstance, user_id: str) -> None:
        creator = session.scalar(select(WorkflowEvent.actor_id).where(
            WorkflowEvent.workflow_instance_id == instance.id,
            WorkflowEvent.event_type == "workflow_created",
        ))
        if not user_id or creator != user_id:
            raise PermissionDenied("Only the submitter can start or cancel this workflow")

    def _actionable_step(self, session: Session, instance_id: int, step_id: int, user_id: str):
        instance = self._instance(session, instance_id)
        step = session.get(WorkflowStep, step_id)
        if step is None or step.workflow_instance_id != instance.id:
            raise NotFound("Step does not exist in this workflow")
        self._require_status(instance, WorkflowStatus.RUNNING)
        if step.status != StepStatus.ACTIVE:
            raise InvalidTransition("Only an active step can be actioned")
        if not user_id or step.assigned_to != user_id:
            raise PermissionDenied("Only the assigned reviewer can act on this step")
        return instance, step

    def _skip_unfinished(self, session: Session, instance: WorkflowInstance, user_id: str) -> None:
        for step in self._steps(session, instance.id):
            if step.status in (StepStatus.PENDING, StepStatus.ACTIVE):
                step.status = StepStatus.SKIPPED
                self._event(session, instance, "step_skipped", user_id, step)

    def _event(self, session: Session, instance: WorkflowInstance, event_type: str,
               user_id: str, step: WorkflowStep | None = None, metadata: dict[str, Any] | None = None) -> None:
        session.add(WorkflowEvent(
            workflow_instance_id=instance.id, step_id=step.id if step else None,
            event_type=event_type, actor_id=user_id, event_metadata=metadata or {},
        ))

    def _state(self, session: Session, instance: WorkflowInstance) -> WorkflowState:
        session.flush()
        return WorkflowState(
            id=instance.id, workflow_definition_id=instance.workflow_definition_id,
            entity_type=instance.entity_type, entity_id=instance.entity_id, status=instance.status,
            created_at=instance.created_at, completed_at=instance.completed_at,
            steps=[StepState.model_validate(step) for step in self._steps(session, instance.id)],
        )