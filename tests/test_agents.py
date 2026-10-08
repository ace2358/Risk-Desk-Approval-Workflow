import pytest

from workflow_engine.agents import ToolUnavailable, WorkflowAgent, WorkflowTools
from workflow_engine.engine import WorkflowEngine
from workflow_engine.models import WorkflowStatus


def test_read_tools_return_isolated_snapshots(sessions):
    engine = WorkflowEngine(sessions)
    assignments = {"Engineer Approval": "engineer", "Manager Approval": "manager", "Final Approval": "final"}
    state = engine.create_workflow("application", "app-1", assignments, "owner")
    application = {"name": "Example", "owners": ["owner"]}
    roles = ["engineer", "manager"]
    tools = WorkflowTools(engine.get_workflow_state, lambda _type, _id: application, lambda: roles)
    snapshot = tools.get_workflow_state(state.id)
    snapshot.status = WorkflowStatus.COMPLETED
    tools.get_application("application", "app-1")["owners"].clear()
    tools.get_available_roles().clear()
    assert engine.get_workflow_state(state.id).status == WorkflowStatus.PENDING
    assert application["owners"] == ["owner"]
    assert roles == ["engineer", "manager"]


def test_future_mutation_tools_are_explicitly_disabled(sessions):
    tools = WorkflowTools(WorkflowEngine(sessions).get_workflow_state)
    for action in [
        lambda: tools.add_approval(1, "security"),
        lambda: tools.request_information(1, "Provide a threat model"),
        lambda: tools.escalate_workflow(1, "Needs review"),
        lambda: tools.get_application("application", "app-1"),
        tools.get_available_roles,
    ]:
        with pytest.raises(ToolUnavailable):
            action()


def test_agent_requires_an_implementation(sessions):
    with pytest.raises(TypeError):
        WorkflowAgent(WorkflowTools(WorkflowEngine(sessions).get_workflow_state))