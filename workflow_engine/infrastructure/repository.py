from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from workflow_engine.application.contracts import CreateWorkflowCommand
from workflow_engine.domain.definitions import WorkflowDefinition as DefinitionSpec
from workflow_engine.domain.definitions import ApprovalWorkflowDefinition, SavedApprovalDefinition
from workflow_engine.domain.types import InvalidInput, NotFound, StepStatus, WorkflowStatus
from workflow_engine.domain.workflow import (
    ApprovalAssignment, StepExecution, Transition, WorkflowEvent as DomainEvent, WorkflowExecution,
)
from workflow_engine.infrastructure.models import WorkflowDefinition, WorkflowEvent, WorkflowInstance, WorkflowStep, WorkflowTransition


class SQLAlchemyWorkflowRepository:
    def __init__(self, session: Session):
        self.session = session
        self._saved_events: dict[int, tuple[WorkflowExecution, int]] = {}

    def save_definition(self, definition: ApprovalWorkflowDefinition) -> SavedApprovalDefinition:
        existing = self.session.scalar(select(WorkflowDefinition).where(
            WorkflowDefinition.name == definition.name, WorkflowDefinition.version == definition.version,
        ))
        steps = [step.model_dump(mode="json") for step in definition.steps]
        if existing is not None:
            if existing.approval_steps != steps:
                raise InvalidInput("Definition version already exists; increment the workflow version")
            return self.get_definition(existing.id)
        stored = self._definition(definition.execution_definition())
        stored.approval_steps = steps
        self.session.flush()
        return SavedApprovalDefinition(id=stored.id, **definition.model_dump())

    def get_definition(self, definition_id: int) -> SavedApprovalDefinition:
        definition = self.session.get(WorkflowDefinition, definition_id)
        if definition is None or definition.approval_steps is None:
            raise NotFound("Saved approval definition does not exist")
        return SavedApprovalDefinition(
            id=definition.id, name=definition.name, version=definition.version, steps=definition.approval_steps,
        )

    def list_definitions(self) -> list[SavedApprovalDefinition]:
        return [self.get_definition(definition.id) for definition in self.session.scalars(
            select(WorkflowDefinition).where(WorkflowDefinition.approval_steps.is_not(None))
            .order_by(WorkflowDefinition.id.desc()),
        )]

    def _steps(self, instance_id: int) -> list[WorkflowStep]:
        return list(self.session.scalars(select(WorkflowStep).where(
            WorkflowStep.workflow_instance_id == instance_id,
        ).order_by(WorkflowStep.order)))

    def _transitions(self, definition_id: int) -> list[WorkflowTransition]:
        return list(self.session.scalars(select(WorkflowTransition).where(
            WorkflowTransition.workflow_definition_id == definition_id,
        )))

    def _definition(self, spec: DefinitionSpec) -> WorkflowDefinition:
        definition = self.session.scalar(select(WorkflowDefinition).where(
            WorkflowDefinition.name == spec.name, WorkflowDefinition.version == spec.version,
        ))
        if definition is None:
            definition = WorkflowDefinition(name=spec.name, version=spec.version)
            self.session.add(definition)
            self.session.flush()
            for current, following in zip(spec.steps, spec.steps[1:]):
                self.session.add(WorkflowTransition(
                    workflow_definition_id=definition.id, from_step=current.name,
                    to_step=following.name, condition="always",
                ))
        else:
            stored_transitions = {(item.from_step, item.to_step, item.condition)
                                  for item in self._transitions(definition.id)}
            sample = self.session.scalar(select(WorkflowInstance).where(
                WorkflowInstance.workflow_definition_id == definition.id,
            ).order_by(WorkflowInstance.id))
            stored_steps = [(step.name, step.step_type) for step in self._steps(sample.id)] if sample else None
            if definition.approval_steps is not None:
                stored_steps = [(step["name"], "approval") for step in definition.approval_steps]
            spec.require_same_version(stored_transitions, stored_steps)
        return definition

    def create(self, definition: DefinitionSpec, command: CreateWorkflowCommand) -> WorkflowExecution:
        stored = self._definition(definition)
        instance = WorkflowInstance(
            workflow_definition_id=stored.id, entity_type=command.entity_type,
            entity_id=command.entity_id, status=WorkflowStatus.PENDING,
        )
        self.session.add(instance)
        self.session.flush()
        for order, step in enumerate(definition.steps, 1):
            self.session.add(WorkflowStep(
                workflow_instance_id=instance.id, name=step.name, step_type=step.step_type,
                status=StepStatus.PENDING, order=order, assigned_to=command.assignments[step.name],
                original_assigned_to=command.assignments[step.name],
            ))
        self.session.flush()
        workflow = self._execution(instance)
        workflow.submitter = command.user_id
        return workflow

    def get(self, instance_id: int) -> WorkflowExecution:
        instance = self.session.get(WorkflowInstance, instance_id)
        if instance is None:
            raise NotFound("Workflow does not exist")
        return self._execution(instance)

    def _execution(self, instance: WorkflowInstance) -> WorkflowExecution:
        submitter = self.session.scalar(select(WorkflowEvent.actor_id).where(
            WorkflowEvent.workflow_instance_id == instance.id, WorkflowEvent.event_type == "workflow_created",
        ))
        return WorkflowExecution(
            id=instance.id, workflow_definition_id=instance.workflow_definition_id,
            entity_type=instance.entity_type, entity_id=instance.entity_id, status=instance.status,
            created_at=instance.created_at, completed_at=instance.completed_at, submitter=submitter,
            steps=[StepExecution(
                step.id, step.workflow_instance_id, step.name, step.step_type, step.status,
                step.order, ApprovalAssignment(step.assigned_to, step.original_assigned_to),
            ) for step in self._steps(instance.id)],
            transitions=tuple(Transition(item.from_step, item.to_step, item.condition)
                              for item in self._transitions(instance.workflow_definition_id)),
        )

    def list(self) -> list[WorkflowExecution]:
        return [self._execution(instance) for instance in self.session.scalars(
            select(WorkflowInstance).order_by(WorkflowInstance.id.desc()),
        )]

    def events(self, instance_id: int) -> list[DomainEvent]:
        self.get(instance_id)
        return [DomainEvent(
            event.workflow_instance_id, event.step_id, event.event_type, event.actor_id,
            event.event_metadata, event.created_at, event.id,
        ) for event in self.session.scalars(select(WorkflowEvent).where(
            WorkflowEvent.workflow_instance_id == instance_id,
        ).order_by(WorkflowEvent.id))]

    def save(self, workflow: WorkflowExecution) -> None:
        instance = self.session.get(WorkflowInstance, workflow.id)
        instance.status = workflow.status
        instance.completed_at = workflow.completed_at
        for step in workflow.steps:
            stored = self.session.get(WorkflowStep, step.id)
            stored.status = step.status
            stored.assigned_to = step.assigned_to
        saved_count = self._saved_events.get(id(workflow), (workflow, 0))[1]
        for event in workflow.pending_events[saved_count:]:
            self.session.add(WorkflowEvent(
                workflow_instance_id=event.workflow_instance_id, step_id=event.step_id,
                event_type=event.event_type, actor_id=event.actor_id,
                created_at=event.created_at, event_metadata=event.event_metadata,
            ))
        self.session.flush()
        self._saved_events[id(workflow)] = (workflow, len(workflow.pending_events))

    def confirm_commit(self) -> None:
        for workflow, count in self._saved_events.values():
            del workflow.pending_events[:count]
        self._saved_events.clear()


class SQLAlchemyUnitOfWork:
    def __init__(self, sessions: sessionmaker[Session], write: bool):
        self.sessions = sessions
        self.write = write

    def __enter__(self):
        self.session = self.sessions()
        try:
            self.session.execute(text("BEGIN IMMEDIATE" if self.write else "BEGIN"))
            self.workflows = SQLAlchemyWorkflowRepository(self.session)
            return self
        except Exception:
            self.session.close()
            raise

    def __exit__(self, error_type, error, traceback):
        try:
            if error_type is None and self.write:
                self.session.commit()
                self.workflows.confirm_commit()
            else:
                self.session.rollback()
        except Exception:
            self.session.rollback()
            raise
        finally:
            self.session.close()