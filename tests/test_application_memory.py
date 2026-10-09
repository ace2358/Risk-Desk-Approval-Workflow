from copy import deepcopy
from dataclasses import replace

import pytest

from workflow_engine.agents import WorkflowTools
from workflow_engine.application.service import WorkflowService
from workflow_engine.domain.types import PermissionDenied, StepStatus, WorkflowStatus, utc_now
from workflow_engine.domain.workflow import ApprovalAssignment, StepExecution, Transition, WorkflowExecution


class MemoryRepository:
    def __init__(self):
        self.instances = {}
        self.history = []

    def create(self, definition, command):
        instance_id = len(self.instances) + 1
        return WorkflowExecution(
            instance_id, 1, command.entity_type, command.entity_id, WorkflowStatus.PENDING, utc_now(), None,
            [StepExecution(index, instance_id, step.name, step.step_type, StepStatus.PENDING, index,
                           ApprovalAssignment(command.assignments[step.name], command.assignments[step.name]))
             for index, step in enumerate(definition.steps, 1)],
            tuple(Transition(current.name, following.name)
                  for current, following in zip(definition.steps, definition.steps[1:])),
            command.user_id,
        )

    def get(self, instance_id):
        return deepcopy(self.instances[instance_id])

    def save(self, workflow):
        for event in workflow.pending_events:
            self.history.append(replace(deepcopy(event), id=len(self.history) + 1))
        workflow.pending_events.clear()
        self.instances[workflow.id] = deepcopy(workflow)

    def events(self, instance_id):
        return deepcopy([event for event in self.history if event.workflow_instance_id == instance_id])

    def list(self):
        return [self.get(instance_id) for instance_id in sorted(self.instances, reverse=True)]


class MemoryUnitOfWork:
    def __init__(self, store, write):
        self.store = store
        self.write = write

    def __enter__(self):
        self.workflows = deepcopy(self.store)
        return self

    def __exit__(self, error_type, error, traceback):
        if error_type is None and self.write:
            self.store.instances = self.workflows.instances
            self.store.history = self.workflows.history


def test_application_and_agent_reader_with_in_memory_persistence():
    store = MemoryRepository()
    service = WorkflowService(lambda write: MemoryUnitOfWork(store, write), recipient_is_eligible=lambda user: user == "delegate")
    state = service.create_workflow("application", "memory", {
        "Engineer Approval": "engineer", "Manager Approval": "manager", "Final Approval": "final",
    }, "owner")
    state = service.start_workflow(state.id, "owner")
    for step in state.steps[:-1]:
        state = service.approve_step(state.id, step.id, step.assigned_to)
    state = service.send_back_step(state.id, state.steps[-1].id, state.steps[0].id, "final", "Recheck")
    state = service.forward_approval(state.id, state.steps[0].id, "engineer", "delegate", "On leave")
    history = service.get_events(state.id)
    with pytest.raises(PermissionDenied):
        service.approve_step(state.id, state.steps[0].id, "engineer")
    assert service.get_events(state.id) == history
    assert service.get_workflow_state(state.id) == state
    snapshot = WorkflowTools(service.get_workflow_state).get_workflow_state(state.id)
    snapshot.steps.clear()
    assert len(service.get_workflow_state(state.id).steps) == 3
    for step in state.steps:
        state = service.approve_step(state.id, step.id, step.assigned_to)
    assert state.status == WorkflowStatus.COMPLETED
    assert service.list_workflows() == [state]