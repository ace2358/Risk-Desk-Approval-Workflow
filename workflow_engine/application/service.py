from collections.abc import Callable

from workflow_engine.application.contracts import CreateWorkflowCommand, EventState, WorkflowState
from workflow_engine.application.ports import UnitOfWorkFactory
from workflow_engine.domain.definitions import WorkflowDefinition, technical_risk_workflow
from workflow_engine.domain.types import InvalidInput
from workflow_engine.domain.workflow import WorkflowExecution, require_reason


class WorkflowService:
    def __init__(
        self, unit_of_work: UnitOfWorkFactory, definition: WorkflowDefinition = technical_risk_workflow,
        *, recipient_is_eligible: Callable[[str], bool] | None = None,
    ):
        self.unit_of_work = unit_of_work
        self.definition = definition
        self.recipient_is_eligible = recipient_is_eligible

    def create_workflow(self, entity_type: str, entity_id: str, assignments: dict[str, str], user_id: str) -> WorkflowState:
        command = CreateWorkflowCommand(
            entity_type=entity_type, entity_id=entity_id, assignments=assignments, user_id=user_id,
        )
        expected = {step.name for step in self.definition.steps}
        if set(command.assignments) != expected:
            raise InvalidInput(f"Assignments must contain exactly: {', '.join(sorted(expected))}")
        with self.unit_of_work(True) as transaction:
            workflow = transaction.workflows.create(self.definition, command)
            workflow.record_creation()
            transaction.workflows.save(workflow)
            return WorkflowState.model_validate(workflow)

    def _change(self, instance_id: int, action: Callable[[WorkflowExecution], None]) -> WorkflowState:
        with self.unit_of_work(True) as transaction:
            workflow = transaction.workflows.get(instance_id)
            action(workflow)
            transaction.workflows.save(workflow)
            return WorkflowState.model_validate(workflow)

    def start_workflow(self, instance_id: int, user_id: str) -> WorkflowState:
        return self._change(instance_id, lambda workflow: workflow.start(user_id))

    def approve_step(self, instance_id: int, step_id: int, user_id: str) -> WorkflowState:
        return self._change(instance_id, lambda workflow: workflow.approve(step_id, user_id))

    def send_back_step(self, instance_id: int, step_id: int, target_step_id: int,
                       user_id: str, reason: str) -> WorkflowState:
        reason = require_reason(reason)
        return self._change(instance_id, lambda workflow: workflow.send_back(step_id, target_step_id, user_id, reason))

    def forward_approval(self, instance_id: int, step_id: int, from_user_id: str,
                         to_user_id: str, reason: str) -> WorkflowState:
        reason = require_reason(reason)
        return self._change(instance_id, lambda workflow: workflow.forward(
            step_id, from_user_id, to_user_id, reason, self.recipient_is_eligible,
        ))

    def reject_step(self, instance_id: int, step_id: int, user_id: str, reason: str) -> WorkflowState:
        if not reason.strip():
            raise InvalidInput("Rejection requires a reason")
        return self._change(instance_id, lambda workflow: workflow.reject(step_id, user_id, reason))

    def cancel_workflow(self, instance_id: int, user_id: str) -> WorkflowState:
        return self._change(instance_id, lambda workflow: workflow.cancel(user_id))

    def list_workflows(self) -> list[WorkflowState]:
        with self.unit_of_work(False) as transaction:
            return [WorkflowState.model_validate(workflow) for workflow in transaction.workflows.list()]

    def get_workflow_state(self, instance_id: int) -> WorkflowState:
        with self.unit_of_work(False) as transaction:
            return WorkflowState.model_validate(transaction.workflows.get(instance_id))

    def get_events(self, instance_id: int) -> list[EventState]:
        with self.unit_of_work(False) as transaction:
            return [EventState.model_validate(event) for event in transaction.workflows.events(instance_id)]