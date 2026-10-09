import pytest

from workflow_engine.application.service import WorkflowService
from workflow_engine.domain.types import StepStatus, WorkflowStatus
from workflow_engine.infrastructure.repository import SQLAlchemyUnitOfWork


def test_application_service_uses_transaction_adapter(sessions):
    service = WorkflowService(lambda write: SQLAlchemyUnitOfWork(sessions, write))
    state = service.create_workflow("application", "service", {
        "Engineer Approval": "engineer", "Manager Approval": "manager", "Final Approval": "final",
    }, "owner")
    state = service.start_workflow(state.id, "owner")
    assert state.steps[0].status == StepStatus.ACTIVE
    for step in state.steps:
        state = service.approve_step(state.id, step.id, step.assigned_to)
    assert state.status == WorkflowStatus.COMPLETED
    assert len(service.get_events(state.id)) == 9
    assert service.list_workflows() == [state]


def test_unit_of_work_rolls_back_state_and_events(sessions):
    service = WorkflowService(lambda write: SQLAlchemyUnitOfWork(sessions, write))
    state = service.create_workflow("application", "rollback", {
        "Engineer Approval": "engineer", "Manager Approval": "manager", "Final Approval": "final",
    }, "owner")
    history = service.get_events(state.id)
    with pytest.raises(RuntimeError):
        with SQLAlchemyUnitOfWork(sessions, True) as transaction:
            workflow = transaction.workflows.get(state.id)
            workflow.start("owner")
            transaction.workflows.save(workflow)
            raise RuntimeError("Abort after flush")
    assert service.get_workflow_state(state.id) == state
    assert service.get_events(state.id) == history
    assert len(workflow.pending_events) == 2


def test_repeated_save_does_not_duplicate_events(sessions):
    service = WorkflowService(lambda write: SQLAlchemyUnitOfWork(sessions, write))
    state = service.create_workflow("application", "save-twice", {
        "Engineer Approval": "engineer", "Manager Approval": "manager", "Final Approval": "final",
    }, "owner")
    with SQLAlchemyUnitOfWork(sessions, True) as transaction:
        workflow = transaction.workflows.get(state.id)
        workflow.start("owner")
        transaction.workflows.save(workflow)
        transaction.workflows.save(workflow)
        assert len(workflow.pending_events) == 2
    assert workflow.pending_events == []
    assert len(service.get_events(state.id)) == 3


def test_commit_failure_preserves_pending_events_and_database(sessions, monkeypatch):
    service = WorkflowService(lambda write: SQLAlchemyUnitOfWork(sessions, write))
    state = service.create_workflow("application", "commit-failure", {
        "Engineer Approval": "engineer", "Manager Approval": "manager", "Final Approval": "final",
    }, "owner")
    history = service.get_events(state.id)

    def fail_commit():
        raise RuntimeError("Commit failed")

    with pytest.raises(RuntimeError, match="Commit failed"):
        with SQLAlchemyUnitOfWork(sessions, True) as transaction:
            workflow = transaction.workflows.get(state.id)
            workflow.start("owner")
            transaction.workflows.save(workflow)
            monkeypatch.setattr(transaction.session, "commit", fail_commit)
    assert len(workflow.pending_events) == 2
    assert service.get_workflow_state(state.id) == state
    assert service.get_events(state.id) == history