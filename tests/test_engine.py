from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from workflow_engine.engine import InvalidInput, InvalidTransition, NotFound, PermissionDenied, WorkflowEngine
from workflow_engine.models import StepStatus, WorkflowStatus, WorkflowTransition


ASSIGNMENTS = {"Engineer Approval": "engineer", "Manager Approval": "manager", "Final Approval": "final"}


@pytest.fixture
def engine(sessions):
    return WorkflowEngine(sessions)


@pytest.fixture
def pending(engine):
    return engine.create_workflow("application", "app-1", ASSIGNMENTS, "owner")


@pytest.fixture
def running(engine, pending):
    return engine.start_workflow(pending.id, "owner")


def test_creation_is_pending(engine, pending):
    assert pending.status == WorkflowStatus.PENDING
    assert all(step.status == StepStatus.PENDING for step in pending.steps)
    assert pending.completed_at is None
    assert engine.get_workflow_state(pending.id) == pending


def test_start_activates_only_engineer_approval(engine, pending):
    state = engine.start_workflow(pending.id, "owner")
    assert state.status == WorkflowStatus.RUNNING
    assert [step.status for step in state.steps] == [StepStatus.ACTIVE, StepStatus.PENDING, StepStatus.PENDING]
    assert state.steps[0].name == "Engineer Approval"


def test_approval_activates_next_step(engine, running):
    state = engine.approve_step(running.id, running.steps[0].id, "engineer")
    assert state.status == WorkflowStatus.RUNNING
    assert [step.status for step in state.steps] == [StepStatus.APPROVED, StepStatus.ACTIVE, StepStatus.PENDING]


def test_final_approval_completes_workflow(engine, running):
    for step in running.steps:
        state = engine.approve_step(running.id, step.id, step.assigned_to)
    assert state.status == WorkflowStatus.COMPLETED
    assert state.completed_at is not None
    assert all(step.status == StepStatus.APPROVED for step in state.steps)
    assert engine.get_events(running.id)[-1].event_type == "workflow_completed"


def test_rejection_is_terminal_and_skips_remaining_steps(engine, running):
    state = engine.reject_step(running.id, running.steps[0].id, "engineer", "Missing threat model")
    assert state.status == WorkflowStatus.REJECTED
    assert state.completed_at is not None
    assert [step.status for step in state.steps] == [StepStatus.REJECTED, StepStatus.SKIPPED, StepStatus.SKIPPED]
    event = next(event for event in engine.get_events(state.id) if event.event_type == "step_rejected")
    assert event.metadata == {"reason": "Missing threat model"}


@pytest.mark.parametrize("operation", ["approve", "reject"])
def test_inactive_step_cannot_be_actioned(engine, running, operation):
    before = engine.get_events(running.id)
    with pytest.raises(InvalidTransition):
        if operation == "approve":
            engine.approve_step(running.id, running.steps[1].id, "manager")
        else:
            engine.reject_step(running.id, running.steps[1].id, "manager", "reason")
    assert engine.get_workflow_state(running.id) == running
    assert engine.get_events(running.id) == before


@pytest.mark.parametrize("operation", ["approve", "reject", "cancel"])
def test_unauthorized_actions_leave_no_changes(engine, running, operation):
    before = engine.get_events(running.id)
    with pytest.raises(PermissionDenied):
        if operation == "approve":
            engine.approve_step(running.id, running.steps[0].id, "stranger")
        elif operation == "reject":
            engine.reject_step(running.id, running.steps[0].id, "stranger", "reason")
        else:
            engine.cancel_workflow(running.id, "stranger")
    assert engine.get_workflow_state(running.id) == running
    assert engine.get_events(running.id) == before


def test_only_submitter_can_start(engine, pending):
    with pytest.raises(PermissionDenied):
        engine.start_workflow(pending.id, "stranger")
    assert engine.get_workflow_state(pending.id) == pending


def test_audit_events_record_order_actors_and_steps(engine, running):
    engine.approve_step(running.id, running.steps[0].id, "engineer")
    events = engine.get_events(running.id)
    assert [event.event_type for event in events] == [
        "workflow_created", "workflow_started", "step_activated", "step_approved", "step_activated",
    ]
    assert events[3].actor_id == "engineer"
    assert events[3].step_id == running.steps[0].id
    assert all(event.created_at is not None for event in events)


def test_workflow_cannot_start_twice(engine, running):
    with pytest.raises(InvalidTransition):
        engine.start_workflow(running.id, "owner")


def test_approved_step_cannot_be_approved_twice(engine, running):
    engine.approve_step(running.id, running.steps[0].id, "engineer")
    with pytest.raises(InvalidTransition):
        engine.approve_step(running.id, running.steps[0].id, "engineer")


def test_pending_workflow_cannot_be_approved(engine, pending):
    with pytest.raises(InvalidTransition):
        engine.approve_step(pending.id, pending.steps[0].id, "engineer")


@pytest.mark.parametrize("terminal", ["completed", "rejected", "cancelled"])
def test_terminal_workflows_cannot_change(engine, running, terminal):
    if terminal == "completed":
        for step in running.steps:
            engine.approve_step(running.id, step.id, step.assigned_to)
    elif terminal == "rejected":
        engine.reject_step(running.id, running.steps[0].id, "engineer", "reason")
    else:
        engine.cancel_workflow(running.id, "owner")
    before = engine.get_workflow_state(running.id)
    events = engine.get_events(running.id)
    actions = [
        lambda: engine.start_workflow(running.id, "owner"),
        lambda: engine.approve_step(running.id, running.steps[0].id, "engineer"),
        lambda: engine.reject_step(running.id, running.steps[0].id, "engineer", "reason"),
        lambda: engine.cancel_workflow(running.id, "owner"),
    ]
    for action in actions:
        with pytest.raises(InvalidTransition):
            action()
    assert engine.get_workflow_state(running.id) == before
    assert engine.get_events(running.id) == events


@pytest.mark.parametrize("start", [False, True])
def test_cancel_skips_unfinished_steps(engine, pending, start):
    if start:
        engine.start_workflow(pending.id, "owner")
    state = engine.cancel_workflow(pending.id, "owner")
    assert state.status == WorkflowStatus.CANCELLED
    assert all(step.status == StepStatus.SKIPPED for step in state.steps)
    assert engine.get_events(state.id)[-1].event_type == "workflow_cancelled"


def test_step_from_another_instance_is_not_actionable(engine, running):
    other = engine.create_workflow("application", "app-2", ASSIGNMENTS, "owner")
    with pytest.raises(NotFound):
        engine.approve_step(running.id, other.steps[0].id, "engineer")


def test_missing_workflow_and_step(engine, running):
    with pytest.raises(NotFound):
        engine.start_workflow(99999, "owner")
    with pytest.raises(NotFound):
        engine.approve_step(running.id, 99999, "engineer")


def test_all_reviewers_must_be_assigned(engine):
    with pytest.raises(InvalidInput):
        engine.create_workflow("application", "app-1", {"Engineer Approval": "engineer"}, "owner")


def test_invalid_transition_rolls_back_approval_and_event(engine, running, sessions):
    with sessions.begin() as session:
        transition = session.scalar(select(WorkflowTransition).where(
            WorkflowTransition.from_step == "Engineer Approval",
        ))
        transition.to_step = "Missing step"
    events = engine.get_events(running.id)
    with pytest.raises(InvalidTransition):
        engine.approve_step(running.id, running.steps[0].id, "engineer")
    assert engine.get_workflow_state(running.id) == running
    assert engine.get_events(running.id) == events


def test_concurrent_approvals_only_succeed_once(engine, running):
    def approve_once():
        try:
            engine.approve_step(running.id, running.steps[0].id, "engineer")
            return "approved"
        except InvalidTransition:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: approve_once(), range(2)))
    assert sorted(results) == ["approved", "conflict"]
    assert sum(event.event_type == "step_approved" for event in engine.get_events(running.id)) == 1