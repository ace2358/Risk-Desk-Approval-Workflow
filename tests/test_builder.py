import pytest
from fastapi.testclient import TestClient

from workflow_engine.api import create_app
from workflow_engine.database import initialize_database
from workflow_engine.definitions import StepDefinition, WorkflowDefinition
from workflow_engine.engine import InvalidInput, WorkflowEngine


def definition(count, name="Builder"):
    return {"name": name, "version": 1, "steps": [
        {"id": f"approval-{index}", "name": f"Review {index}", "order": index, "assigned_to": "engineer"}
        for index in range(1, count + 1)
    ]}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f"sqlite:///{tmp_path / 'builder.db'}")) as client:
        yield client


@pytest.mark.parametrize("count", [1, 3, 5, 10])
def test_save_retrieve_and_execute_any_step_count(client, count):
    payload = definition(count)
    response = client.post("/workflow-definitions", json=payload)
    assert response.status_code == 201, response.text
    saved = response.json()
    assert {key: saved[key] for key in payload} == payload
    assert client.get(f"/workflow-definitions/{saved['id']}").json() == saved
    assert client.get("/workflow-definitions").json() == [saved]
    response = client.post(f"/workflow-definitions/{saved['id']}/workflows", json={
        "entity_type": "application", "entity_id": "dynamic", "user_id": "submitter",
    })
    assert response.status_code == 201
    state = response.json()
    assert len(state["steps"]) == count
    assert state["workflow_definition_id"] == saved["id"]
    assert all(step["status"] == "Pending" for step in state["steps"])
    instance_id = state["id"]
    state = client.post(f"/workflows/{instance_id}/start", json={"user_id": "submitter"}).json()
    assert state["steps"][0]["status"] == "Active"
    for index, step in enumerate(state["steps"]):
        response = client.post(f"/workflows/{instance_id}/steps/{step['id']}/approve", json={"user_id": "engineer"})
        assert response.status_code == 200
        current = response.json()
        assert current["status"] == ("Completed" if index == count - 1 else "Running")
        assert sum(step["status"] == "Active" for step in current["steps"]) == (index < count - 1)
    assert len(client.get(f"/workflows/{instance_id}/events").json()) == 2 * count + 3


def test_legacy_definition_column_migration_preserves_existing_data(sessions):
    database = sessions.kw["bind"]
    with database.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE workflow_definitions DROP COLUMN approval_steps")
        connection.exec_driver_sql("INSERT INTO workflow_definitions (name, version) VALUES ('Legacy builder', 1)")
    initialize_database(database)
    initialize_database(database)
    with database.connect() as connection:
        assert connection.exec_driver_sql("SELECT name, version, approval_steps FROM workflow_definitions").one() == (
            "Legacy builder", 1, None,
        )


def test_saved_single_step_version_cannot_be_reused_before_first_instance(sessions):
    from workflow_engine.domain.definitions import ApprovalWorkflowDefinition

    engine = WorkflowEngine(sessions, recipient_is_eligible=lambda user: user == "engineer")
    saved = engine.save_definition(ApprovalWorkflowDefinition.model_validate(definition(1)))
    incompatible = WorkflowEngine(sessions, WorkflowDefinition(
        name=saved.name, version=saved.version, steps=(StepDefinition(name="Changed approval"),),
    ))
    with pytest.raises(InvalidInput, match="increment"):
        incompatible.create_workflow("app", "incompatible", {"Changed approval": "engineer"}, "owner")
    assert engine.list_workflows() == []
    assert engine.get_definition(saved.id) == saved


def test_add_remove_reorder_before_save_and_version_without_mutating_instances(client):
    draft = definition(3)
    draft["steps"].append({"id": "quality-id", "name": "Quality", "order": 4, "assigned_to": "manager"})
    removed = draft["steps"].pop(1)
    draft["steps"] = [draft["steps"][-1], *draft["steps"][:-1]]
    for order, step in enumerate(draft["steps"], 1):
        step["order"] = order
    saved = client.post("/workflow-definitions", json=draft).json()
    assert [step["id"] for step in saved["steps"]] == ["quality-id", "approval-1", "approval-3"]
    assert removed["id"] not in {step["id"] for step in saved["steps"]}
    assert client.get(f"/workflow-definitions/{saved['id']}").json()["steps"] == draft["steps"]
    state = client.post(f"/workflow-definitions/{saved['id']}/workflows", json={
        "entity_type": "app", "entity_id": "before-version-change", "user_id": "submitter",
    }).json()
    assert [step["name"] for step in state["steps"]] == ["Quality", "Review 1", "Review 3"]
    assert client.post("/workflow-definitions", json=draft).json()["id"] == saved["id"]
    draft["steps"][0]["assigned_to"] = "final"
    assert client.post("/workflow-definitions", json=draft).status_code == 422
    draft["version"] = 2
    revised = client.post("/workflow-definitions", json=draft).json()
    assert revised["id"] != saved["id"]
    assert client.get(f"/workflows/{state['id']}").json() == state
    assert client.get(f"/workflow-definitions/{saved['id']}").json() == saved


@pytest.mark.parametrize("invalid", [
    {"steps": []}, {"name": "  "}, {"version": 0}, {"version": 1.5},
    {"step": {"name": "  "}}, {"step": {"id": "  "}},
    {"step": {"assigned_to": "unknown"}}, {"step": {"assigned_to": " "}},
    {"step": {"order": 0}}, {"step": {"order": 2}}, {"step": {"order": 1.5}},
    {"step": {"id": "approval-2"}}, {"step": {"name": "Review 2"}},
    {"step": {"approver_role": "manager"}},
])
def test_invalid_definitions_are_rejected_without_persisting(client, invalid):
    payload = definition(3)
    if "step" in invalid:
        payload["steps"][0].update(invalid["step"])
    else:
        payload.update(invalid)
    assert client.post("/workflow-definitions", json=payload).status_code == 422
    assert client.get("/workflow-definitions").json() == []
    assert client.get("/workflows").json() == []


def test_unsorted_sequence_is_rejected(client):
    payload = definition(3)
    payload["steps"].reverse()
    assert client.post("/workflow-definitions", json=payload).status_code == 422


def test_saved_definition_survives_application_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'persisted-builder.db'}"
    with TestClient(create_app(url)) as client:
        saved = client.post("/workflow-definitions", json=definition(10)).json()
    with TestClient(create_app(url)) as client:
        assert client.get(f"/workflow-definitions/{saved['id']}").json() == saved
        assert client.get("/workflow-definitions").json() == [saved]


def test_dynamic_repeated_send_back_forward_and_unauthorized_actions(client):
    saved = client.post("/workflow-definitions", json=definition(10)).json()
    state = client.post(f"/workflow-definitions/{saved['id']}/workflows", json={
        "entity_type": "app", "entity_id": "rework-ten", "user_id": "submitter",
    }).json()
    instance = state["id"]
    assert client.post(f"/workflows/{instance}/start", json={"user_id": "observer"}).status_code == 403
    state = client.post(f"/workflows/{instance}/start", json={"user_id": "submitter"}).json()
    for step in state["steps"][:-1]:
        state = client.post(f"/workflows/{instance}/steps/{step['id']}/approve", json={"user_id": "engineer"}).json()
    before = client.get(f"/workflows/{instance}/events").json()
    final_id = state["steps"][-1]["id"]
    for target_index in [0, 3, 0]:
        response = client.post(f"/workflows/{instance}/steps/{final_id}/send-back", json={
            "user_id": "engineer", "target_step_id": state["steps"][target_index]["id"], "reason": "Recheck",
        })
        assert response.status_code == 200
        state = response.json()
        assert state["steps"][target_index]["status"] == "Active"
        assert client.get(f"/workflows/{instance}/events").json()[:len(before)] == before
        for step in state["steps"][target_index:-1]:
            state = client.post(f"/workflows/{instance}/steps/{step['id']}/approve", json={"user_id": "engineer"}).json()
    history = client.get(f"/workflows/{instance}/events").json()
    for action, body in [
        ("approve", {"user_id": "observer"}),
        ("reject", {"user_id": "observer", "reason": "No"}),
        ("forward", {"user_id": "observer", "to_user_id": "manager", "reason": "Delegate"}),
        ("send-back", {"user_id": "observer", "target_step_id": state["steps"][0]["id"], "reason": "Recheck"}),
    ]:
        assert client.post(f"/workflows/{instance}/steps/{final_id}/{action}", json=body).status_code == 403
    assert client.get(f"/workflows/{instance}/events").json() == history
    response = client.post(f"/workflows/{instance}/steps/{final_id}/forward", json={
        "user_id": "engineer", "to_user_id": "manager", "reason": "Reviewer unavailable",
    })
    assert response.status_code == 200
    state = response.json()
    assert state["status"] == "Running"
    assert state["steps"][-1]["status"] == "Active"
    assert state["steps"][-1]["original_assigned_to"] == "engineer"
    event = client.get(f"/workflows/{instance}/events").json()[-1]
    assert event["event_type"] == "approval_forwarded"
    assert event["actor_id"] == "engineer"
    assert event["created_at"]
    assert event["metadata"] == {
        "from_user_id": "engineer", "to_user_id": "manager",
        "original_assigned_to": "engineer", "reason": "Reviewer unavailable",
    }
    assert client.post(f"/workflows/{instance}/steps/{final_id}/approve", json={"user_id": "engineer"}).status_code == 403
    assert client.post(f"/workflows/{instance}/steps/{final_id}/approve", json={"user_id": "manager"}).json()["status"] == "Completed"
    assert client.get(f"/workflow-definitions/{saved['id']}").json() == saved


def test_missing_saved_definition_and_dynamic_rejection(client):
    assert client.get("/workflow-definitions/9999").status_code == 404
    assert client.post("/workflow-definitions/9999/workflows", json={
        "entity_type": "app", "entity_id": "missing", "user_id": "submitter",
    }).status_code == 404
    saved = client.post("/workflow-definitions", json=definition(5)).json()
    state = client.post(f"/workflow-definitions/{saved['id']}/workflows", json={
        "entity_type": "app", "entity_id": "reject-dynamic", "user_id": "submitter",
    }).json()
    instance = state["id"]
    client.post(f"/workflows/{instance}/start", json={"user_id": "submitter"})
    response = client.post(f"/workflows/{instance}/steps/{state['steps'][0]['id']}/reject", json={
        "user_id": "engineer", "reason": "Unacceptable risk",
    })
    assert response.status_code == 200
    assert response.json()["status"] == "Rejected"
    assert all(step["status"] == "Skipped" for step in response.json()["steps"][1:])