from copy import deepcopy

import pytest

from workflow_engine.domain.types import InvalidTransition, PermissionDenied, StepStatus, WorkflowStatus, utc_now
from workflow_engine.domain.workflow import ApprovalAssignment, StepExecution, Transition, WorkflowExecution


def execution():
    return WorkflowExecution(
        id=1, workflow_definition_id=1, entity_type="application", entity_id="test",
        status=WorkflowStatus.PENDING, created_at=utc_now(), completed_at=None,
        steps=[StepExecution(index, 1, f"Step {index}", "approval", StepStatus.PENDING,
                             index, ApprovalAssignment(f"user-{index}", f"user-{index}"))
               for index in range(1, 4)],
        transitions=(Transition("Step 1", "Step 2"), Transition("Step 2", "Step 3")),
        submitter="owner",
    )


def test_domain_rework_forward_and_complete_without_database():
    workflow = execution()
    workflow.record_creation()
    workflow.start("owner")
    workflow.approve(1, "user-1")
    workflow.approve(2, "user-2")
    history = deepcopy(workflow.pending_events)
    workflow.send_back(3, 1, "user-3", "Recheck")
    assert workflow.pending_events[:len(history)] == history
    assert [step.status for step in workflow.steps] == [StepStatus.ACTIVE, StepStatus.PENDING, StepStatus.PENDING]
    workflow.forward(1, "user-1", "delegate", "On leave", lambda user: user == "delegate")
    with pytest.raises(PermissionDenied):
        workflow.approve(1, "user-1")
    assert workflow.steps[0].original_assigned_to == "user-1"
    for step in workflow.steps:
        workflow.approve(step.id, step.assigned_to)
    assert workflow.status == WorkflowStatus.COMPLETED


def test_domain_broken_transition_does_not_mutate_execution():
    workflow = execution()
    workflow.start("owner")
    workflow.transitions = (Transition("Step 1", "Missing"),)
    before = deepcopy(workflow)
    with pytest.raises(InvalidTransition):
        workflow.approve(1, "user-1")
    assert workflow == before


@pytest.mark.parametrize("operation,status", [("reject", WorkflowStatus.REJECTED), ("cancel", WorkflowStatus.CANCELLED)])
def test_domain_terminal_operations_without_database(operation, status):
    workflow = execution()
    workflow.start("owner")
    if operation == "reject":
        workflow.reject(1, "user-1", "Risk")
    else:
        workflow.cancel("owner")
    assert workflow.status == status
    assert workflow.completed_at is not None
    assert all(step.status != StepStatus.ACTIVE for step in workflow.steps)