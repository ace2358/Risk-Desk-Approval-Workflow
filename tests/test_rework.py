import pytest
from pydantic import ValidationError
from sqlalchemy import select

from workflow_engine.definitions import StepDefinition, WorkflowDefinition
from workflow_engine.engine import InvalidInput, InvalidTransition, NotFound, PermissionDenied, WorkflowEngine
from workflow_engine.models import StepStatus, WorkflowStatus, WorkflowTransition


def make_engine(sessions, count=5):
    definition = WorkflowDefinition(
        name=f"Sequential {count}", version=1,
        steps=tuple(StepDefinition(name=f"Step {index}") for index in range(1, count + 1)),
    )
    return WorkflowEngine(sessions, definition, recipient_is_eligible=lambda user: user in {"sarah", "alex"})


def start(engine):
    assignments = {step.name: f"reviewer-{index}" for index, step in enumerate(engine.definition.steps, 1)}
    state = engine.create_workflow("application", "rework-example", assignments, "owner")
    return engine.start_workflow(state.id, "owner")


@pytest.mark.parametrize("count", [1, 2, 5, 10])
def test_any_number_of_steps_completes(sessions, count):
    engine = make_engine(sessions, count)
    state = start(engine)
    assert len(state.steps) == count
    assert [step.status for step in state.steps] == [StepStatus.ACTIVE] + [StepStatus.PENDING] * (count - 1)
    for index, step in enumerate(state.steps):
        state = engine.approve_step(state.id, step.id, step.assigned_to)
        if index < count - 1:
            assert state.status == WorkflowStatus.RUNNING
            assert state.steps[index + 1].status == StepStatus.ACTIVE
        assert sum(item.status == StepStatus.ACTIVE for item in state.steps) == (index < count - 1)
    assert state.status == WorkflowStatus.COMPLETED


def test_repeated_send_back_and_forward_can_complete(sessions):
    engine = make_engine(sessions)
    state = start(engine)
    for step in state.steps[:-1]:
        state = engine.approve_step(state.id, step.id, step.assigned_to)
    history = engine.get_events(state.id)
    for target_index in [0, 1, 0]:
        state = engine.send_back_step(state.id, state.steps[-1].id, state.steps[target_index].id, "reviewer-5", " Recheck risks ")
        assert state.status == WorkflowStatus.RUNNING
        assert state.completed_at is None
        assert state.steps[target_index].status == StepStatus.ACTIVE
        assert all(step.status == StepStatus.PENDING for step in state.steps[target_index + 1:])
        assert engine.get_events(state.id)[:len(history)] == history
        for step in state.steps[target_index:-1]:
            state = engine.approve_step(state.id, step.id, step.assigned_to)
    state = engine.forward_approval(state.id, state.steps[-1].id, "reviewer-5", "sarah", "Reviewer on leave")
    assert state.steps[-1].status == StepStatus.ACTIVE
    assert state.steps[-1].assigned_to == "sarah"
    assert state.steps[-1].original_assigned_to == "reviewer-5"
    state = engine.approve_step(state.id, state.steps[-1].id, "sarah")
    assert state.status == WorkflowStatus.COMPLETED


def test_definition_requires_at_least_one_step():
    with pytest.raises(ValidationError):
        WorkflowDefinition(name="Empty", version=1, steps=())


def reach(engine, index):
    state = start(engine)
    for step in state.steps[:index]:
        state = engine.approve_step(state.id, step.id, step.assigned_to)
    return state


def assert_unchanged(engine, state, action, error):
    history = engine.get_events(state.id)
    with pytest.raises(error):
        action()
    assert engine.get_workflow_state(state.id) == state
    assert engine.get_events(state.id) == history


@pytest.mark.parametrize("source,target", [(2, 0), (4, 0), (4, 2)])
def test_send_back_resets_only_target_and_downstream(sessions, source, target):
    engine = make_engine(sessions)
    state = reach(engine, source)
    history = engine.get_events(state.id)
    previous = state
    state = engine.send_back_step(state.id, state.steps[source].id, state.steps[target].id,
                                  state.steps[source].assigned_to, "  Mitigation incomplete  ")
    assert state.status == WorkflowStatus.RUNNING
    assert state.completed_at is None
    assert [step.status for step in state.steps] == (
        [StepStatus.APPROVED] * target + [StepStatus.ACTIVE] + [StepStatus.PENDING] * (4 - target)
    )
    events = engine.get_events(state.id)
    assert events[:len(history)] == history
    assert [event.event_type for event in events[-2:]] == ["step_sent_back", "step_activated"]
    event = events[-2]
    assert event.step_id == state.steps[source].id
    assert event.actor_id == previous.steps[source].assigned_to
    assert event.metadata == {
        "target_step_id": state.steps[target].id, "reason": "Mitigation incomplete",
        "reset_steps": [
            {"step_id": step.id, "previous_status": step.status.value, "status": "Pending"}
            for step in previous.steps[target:]
        ],
    }


@pytest.mark.parametrize("target_index", [2, 3, 4])
def test_send_back_cannot_target_current_or_future_steps(sessions, target_index):
    engine = make_engine(sessions)
    state = reach(engine, 2)
    assert_unchanged(engine, state, lambda: engine.send_back_step(
        state.id, state.steps[2].id, state.steps[target_index].id, "reviewer-3", "Rework",
    ), InvalidTransition)


def test_send_back_rejects_missing_and_cross_instance_targets(sessions):
    engine = make_engine(sessions)
    state = reach(engine, 2)
    other = start(engine)
    for target_id in [99999, other.steps[0].id]:
        assert_unchanged(engine, state, lambda: engine.send_back_step(
            state.id, state.steps[2].id, target_id, "reviewer-3", "Rework",
        ), NotFound)


def test_send_back_requires_a_definition_path(sessions):
    engine = make_engine(sessions)
    state = reach(engine, 2)
    with sessions.begin() as session:
        transition = session.scalar(select(WorkflowTransition).where(
            WorkflowTransition.from_step == "Step 1",
        ))
        transition.to_step = "Step 5"
    assert_unchanged(engine, state, lambda: engine.send_back_step(
        state.id, state.steps[2].id, state.steps[0].id, "reviewer-3", "Rework",
    ), InvalidTransition)


def test_forward_preserves_original_assignee_and_does_not_approve(sessions):
    engine = make_engine(sessions, 1)
    state = start(engine)
    before = engine.get_events(state.id)
    state = engine.forward_approval(state.id, state.steps[0].id, "reviewer-1", "sarah", " On leave ")
    step = state.steps[0]
    assert state.status == WorkflowStatus.RUNNING
    assert state.completed_at is None
    assert step.status == StepStatus.ACTIVE
    assert step.assigned_to == "sarah"
    assert step.original_assigned_to == "reviewer-1"
    events = engine.get_events(state.id)
    assert events[:-1] == before
    assert events[-1].event_type == "approval_forwarded"
    assert events[-1].actor_id == "reviewer-1"
    assert events[-1].metadata == {
        "from_user_id": "reviewer-1", "to_user_id": "sarah",
        "original_assigned_to": "reviewer-1", "reason": "On leave",
    }
    assert_unchanged(engine, state, lambda: engine.approve_step(state.id, step.id, "reviewer-1"), PermissionDenied)
    assert engine.approve_step(state.id, step.id, "sarah").status == WorkflowStatus.COMPLETED


def test_multiple_forwards_preserve_first_assignee(sessions):
    engine = make_engine(sessions, 1)
    state = start(engine)
    step_id = state.steps[0].id
    state = engine.forward_approval(state.id, step_id, "reviewer-1", "sarah", "Unavailable")
    state = engine.forward_approval(state.id, step_id, "sarah", "alex", "Shift change")
    assert state.steps[0].original_assigned_to == "reviewer-1"
    assert state.steps[0].assigned_to == "alex"
    assert state.steps[0].status == StepStatus.ACTIVE
    assert_unchanged(engine, state, lambda: engine.forward_approval(
        state.id, step_id, "sarah", "sarah", "Return",
    ), PermissionDenied)
    assert engine.approve_step(state.id, step_id, "alex").status == WorkflowStatus.COMPLETED
    forwarded = [event for event in engine.get_events(state.id) if event.event_type == "approval_forwarded"]
    assert [(event.actor_id, event.metadata["to_user_id"]) for event in forwarded] == [
        ("reviewer-1", "sarah"), ("sarah", "alex"),
    ]


@pytest.mark.parametrize("recipient", ["unknown", "", "  ", "reviewer-1"])
def test_invalid_forward_recipient_is_rejected(sessions, recipient):
    engine = make_engine(sessions)
    state = start(engine)
    assert_unchanged(engine, state, lambda: engine.forward_approval(
        state.id, state.steps[0].id, "reviewer-1", recipient, "On leave",
    ), InvalidInput)


def test_forward_without_eligibility_provider_is_denied(sessions):
    engine = make_engine(sessions, 1)
    state = start(engine)
    engine.recipient_is_eligible = None
    assert_unchanged(engine, state, lambda: engine.forward_approval(
        state.id, state.steps[0].id, "reviewer-1", "sarah", "On leave",
    ), InvalidInput)


@pytest.mark.parametrize("operation", ["send_back", "forward"])
def test_rework_requires_current_assignee(sessions, operation):
    engine = make_engine(sessions)
    state = reach(engine, 2)
    action = (lambda: engine.send_back_step(state.id, state.steps[2].id, state.steps[0].id, "stranger", "Rework")) if operation == "send_back" else (
        lambda: engine.forward_approval(state.id, state.steps[2].id, "stranger", "sarah", "On leave")
    )
    assert_unchanged(engine, state, action, PermissionDenied)


@pytest.mark.parametrize("reason", ["", " \n "])
def test_rework_requires_a_reason(sessions, reason):
    engine = make_engine(sessions)
    state = reach(engine, 2)
    assert_unchanged(engine, state, lambda: engine.send_back_step(
        state.id, state.steps[2].id, state.steps[0].id, "reviewer-3", reason,
    ), InvalidInput)
    assert_unchanged(engine, state, lambda: engine.forward_approval(
        state.id, state.steps[2].id, "reviewer-3", "sarah", reason,
    ), InvalidInput)


@pytest.mark.parametrize("status", ["pending", "inactive", "completed", "rejected", "cancelled"])
def test_rework_rejects_inactive_and_terminal_states(sessions, status):
    engine = make_engine(sessions)
    state = start(engine) if status != "pending" else engine.create_workflow(
        "application", "pending", {step.name: "reviewer-1" for step in engine.definition.steps}, "owner",
    )
    if status == "completed":
        for step in state.steps:
            state = engine.approve_step(state.id, step.id, step.assigned_to)
    elif status == "rejected":
        state = engine.reject_step(state.id, state.steps[0].id, "reviewer-1", "Reason")
    elif status == "cancelled":
        state = engine.cancel_workflow(state.id, "owner")
    step = state.steps[1] if status == "inactive" else state.steps[0]
    assert_unchanged(engine, state, lambda: engine.send_back_step(
        state.id, step.id, state.steps[0].id, step.assigned_to, "Rework",
    ), InvalidTransition)
    assert_unchanged(engine, state, lambda: engine.forward_approval(
        state.id, step.id, step.assigned_to, "sarah", "On leave",
    ), InvalidTransition)


def test_rework_checks_workflow_and_step_membership(sessions):
    engine = make_engine(sessions)
    state = reach(engine, 2)
    other = start(engine)
    for instance_id, step_id in [(99999, state.steps[2].id), (state.id, 99999), (state.id, other.steps[0].id)]:
        with pytest.raises(NotFound):
            engine.send_back_step(instance_id, step_id, state.steps[0].id, "reviewer-3", "Rework")
        with pytest.raises(NotFound):
            engine.forward_approval(instance_id, step_id, "reviewer-3", "sarah", "On leave")


def test_definition_changes_require_a_new_version(sessions):
    engine = make_engine(sessions, 2)
    start(engine)
    changed = engine.definition.model_copy(update={"steps": (StepDefinition(name="Different"),)})
    with pytest.raises(InvalidInput, match="version"):
        WorkflowEngine(sessions, changed).create_workflow("app", "2", {"Different": "john"}, "owner")
    versioned = changed.model_copy(update={"version": 2})
    assert len(WorkflowEngine(sessions, versioned).create_workflow("app", "2", {"Different": "john"}, "owner").steps) == 1


def test_combined_five_step_flow_has_complete_reconstructible_history(sessions):
    names = ["Engineer", "Manager", "Quality", "Safety", "Final Approval"]
    definition = WorkflowDefinition(name="Combined", version=1, steps=tuple(StepDefinition(name=name) for name in names))
    engine = WorkflowEngine(sessions, definition, recipient_is_eligible=lambda user: user == "sarah")
    assignments = dict(zip(names, ["engineer", "john", "quality", "safety", "final"]))
    state = engine.create_workflow("application", "combined", assignments, "owner")
    state = engine.start_workflow(state.id, "owner")
    expected = [("workflow_created", None, "owner"), ("workflow_started", None, "owner"),
                ("step_activated", state.steps[0].id, "owner")]
    for step in state.steps[:-1]:
        state = engine.approve_step(state.id, step.id, step.assigned_to)
        expected += [("step_approved", step.id, step.assigned_to),
                     ("step_activated", state.steps[step.order].id, step.assigned_to)]
    prefix = engine.get_events(state.id)
    state = engine.send_back_step(state.id, state.steps[-1].id, state.steps[0].id, "final", "Risk mitigation incomplete")
    expected += [("step_sent_back", state.steps[-1].id, "final"), ("step_activated", state.steps[0].id, "final")]
    assert engine.get_events(state.id)[:len(prefix)] == prefix
    state = engine.approve_step(state.id, state.steps[0].id, "engineer")
    expected += [("step_approved", state.steps[0].id, "engineer"), ("step_activated", state.steps[1].id, "engineer")]
    state = engine.forward_approval(state.id, state.steps[1].id, "john", "sarah", "John is on leave")
    expected += [("approval_forwarded", state.steps[1].id, "john")]
    assert state.steps[1].status == StepStatus.ACTIVE
    for step in state.steps[1:]:
        state = engine.approve_step(state.id, step.id, step.assigned_to)
        expected += [("step_approved", step.id, step.assigned_to)]
        if step.order < len(state.steps):
            expected += [("step_activated", state.steps[step.order].id, step.assigned_to)]
        else:
            expected += [("workflow_completed", None, step.assigned_to)]
    assert state.status == WorkflowStatus.COMPLETED
    assert all(step.status == StepStatus.APPROVED for step in state.steps)
    assert state.steps[1].assigned_to == "sarah"
    assert state.steps[1].original_assigned_to == "john"
    events = engine.get_events(state.id)
    assert [(event.event_type, event.step_id, event.actor_id) for event in events] == expected
    replay_status = {step.id: StepStatus.PENDING for step in state.steps}
    replay_assignment = {item["step_id"]: item["assigned_to"] for item in events[0].metadata["assignments"]}
    for event in events:
        if event.event_type == "step_activated":
            replay_status[event.step_id] = StepStatus.ACTIVE
        elif event.event_type == "step_approved":
            replay_status[event.step_id] = StepStatus.APPROVED
        elif event.event_type == "step_sent_back":
            for item in event.metadata["reset_steps"]:
                replay_status[item["step_id"]] = StepStatus(item["status"])
        elif event.event_type == "approval_forwarded":
            replay_assignment[event.step_id] = event.metadata["to_user_id"]
    assert [(replay_status[step.id], replay_assignment[step.id]) for step in state.steps] == [
        (step.status, step.assigned_to) for step in state.steps
    ]