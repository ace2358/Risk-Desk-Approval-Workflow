import pytest
from fastapi.testclient import TestClient

from workflow_engine.api import create_app
from workflow_engine.definitions import StepDefinition, WorkflowDefinition


SUBMISSION = {
    "entity_type": "application", "entity_id": "app-123", "user_id": "submitter",
    "assignments": {
        "Engineer Approval": "engineer", "Manager Approval": "manager", "Final Approval": "final",
    },
}


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(f"sqlite:///{tmp_path / 'api.db'}")) as client:
        yield client


def create_pending(client):
    response = client.post("/workflows", json=SUBMISSION)
    assert response.status_code == 201
    return response.json()


def test_complete_workflow_through_api(client):
    state = create_pending(client)
    instance_id = state["id"]
    assert state["status"] == "Pending"
    response = client.post(f"/workflows/{instance_id}/start", json={"user_id": "submitter"})
    assert response.status_code == 200
    assert response.json()["steps"][0]["status"] == "Active"
    for step in state["steps"]:
        response = client.post(
            f"/workflows/{instance_id}/steps/{step['id']}/approve", json={"user_id": step["assigned_to"]},
        )
        assert response.status_code == 200
    assert response.json()["status"] == "Completed"
    assert response.json()["completed_at"] is not None
    assert client.get(f"/workflows/{instance_id}").json() == response.json()
    events = client.get(f"/workflows/{instance_id}/events")
    assert events.status_code == 200
    assert events.json()[-1]["event_type"] == "workflow_completed"
    assert "metadata" in events.json()[0]
    assert "event_metadata" not in events.json()[0]


def test_reject_through_api(client):
    state = create_pending(client)
    instance_id = state["id"]
    client.post(f"/workflows/{instance_id}/start", json={"user_id": "submitter"})
    response = client.post(
        f"/workflows/{instance_id}/steps/{state['steps'][0]['id']}/reject",
        json={"user_id": "engineer", "reason": "Missing security review"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "Rejected"


def test_cancel_through_api(client):
    state = create_pending(client)
    response = client.post(f"/workflows/{state['id']}/cancel", json={"user_id": "submitter"})
    assert response.status_code == 200
    assert response.json()["status"] == "Cancelled"


def test_engine_errors_have_meaningful_http_status_codes(client):
    state = create_pending(client)
    instance_id = state["id"]
    assert client.get("/workflows/99999").status_code == 404
    assert client.get("/workflows/99999/events").status_code == 404
    assert client.post(f"/workflows/{instance_id}/start", json={"user_id": "outsider"}).status_code == 403
    assert client.post(f"/workflows/{instance_id}/start", json={"user_id": "submitter"}).status_code == 200
    assert client.post(f"/workflows/{instance_id}/start", json={"user_id": "submitter"}).status_code == 409
    path = f"/workflows/{instance_id}/steps/{state['steps'][0]['id']}/approve"
    assert client.post(path, json={"user_id": "outsider"}).status_code == 403
    assert client.post(f"/workflows/{instance_id}/steps/99999/approve", json={"user_id": "engineer"}).status_code == 404


@pytest.mark.parametrize("changes", [
    {"user_id": " "}, {"entity_id": ""}, {"assignments": {}},
    {"assignments": {"Engineer Approval": ""}}, {"status": "Completed"},
])
def test_invalid_submission_is_rejected(client, changes):
    response = client.post("/workflows", json=SUBMISSION | changes)
    assert response.status_code == 422


def test_missing_actor_and_empty_rejection_reason_are_rejected(client):
    state = create_pending(client)
    assert client.post(f"/workflows/{state['id']}/start", json={}).status_code == 422
    path = f"/workflows/{state['id']}/steps/{state['steps'][0]['id']}/reject"
    assert client.post(path, json={"user_id": "engineer", "reason": " "}).status_code == 422


def test_state_survives_application_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'persistent.db'}"
    with TestClient(create_app(url)) as client:
        state = create_pending(client)
    with TestClient(create_app(url)) as client:
        response = client.get(f"/workflows/{state['id']}")
        assert response.status_code == 200
        assert response.json() == state


def test_workflow_list_is_newest_first_and_does_not_mutate(client):
    assert client.get("/workflows").json() == []
    first = create_pending(client)
    second = create_pending(client)
    before = client.get(f"/workflows/{first['id']}/events").json()
    response = client.get("/workflows")
    assert response.status_code == 200
    assert response.json() == [second, first]
    assert client.get(f"/workflows/{first['id']}/events").json() == before


def test_mock_users_do_not_grant_approval_permissions(client):
    response = client.get("/demo/users")
    assert response.status_code == 200
    assert {user["id"] for user in response.json()} == {"submitter", "engineer", "manager", "final", "observer"}
    state = create_pending(client)
    client.post(f"/workflows/{state['id']}/start", json={"user_id": "submitter"})
    path = f"/workflows/{state['id']}/steps/{state['steps'][0]['id']}/approve"
    assert client.post(path, json={"user_id": "observer"}).status_code == 403


def test_local_workbench_and_assets_are_served(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Risk Desk" in response.text
    assert "Mock user" in response.text
    for name in ["app.js", "styles.css", "workflow-mark.svg", "workflow-diagram.svg"]:
        assert client.get(f"/static/{name}").status_code == 200
    assert client.get("/static/missing.js").status_code == 404


def test_send_back_and_forward_through_api(client):
    state = create_pending(client)
    instance = state["id"]
    client.post(f"/workflows/{instance}/start", json={"user_id": "submitter"})
    first, manager, final = state["steps"]
    for step in [first, manager]:
        assert client.post(f"/workflows/{instance}/steps/{step['id']}/approve", json={"user_id": step["assigned_to"]}).status_code == 200
    before = client.get(f"/workflows/{instance}/events").json()
    path = f"/workflows/{instance}/steps/{final['id']}/send-back"
    response = client.post(path, json={"user_id": "final", "target_step_id": first["id"], "reason": "Incomplete risks"})
    assert response.status_code == 200
    assert [step["status"] for step in response.json()["steps"]] == ["Active", "Pending", "Pending"]
    assert client.get(f"/workflows/{instance}/events").json()[:len(before)] == before
    path = f"/workflows/{instance}/steps/{first['id']}/forward"
    response = client.post(path, json={"user_id": "engineer", "to_user_id": "observer", "reason": "Engineer unavailable"})
    assert response.status_code == 200
    assert response.json()["steps"][0]["status"] == "Active"
    assert response.json()["steps"][0]["assigned_to"] == "observer"
    assert response.json()["steps"][0]["original_assigned_to"] == "engineer"
    approve = f"/workflows/{instance}/steps/{first['id']}/approve"
    assert client.post(approve, json={"user_id": "engineer"}).status_code == 403
    assert client.post(approve, json={"user_id": "observer"}).status_code == 200


@pytest.mark.parametrize("operation,body,status", [
    ("forward", {"user_id": "observer", "to_user_id": "manager", "reason": "Leave"}, 403),
    ("forward", {"user_id": "engineer", "to_user_id": "unknown", "reason": "Leave"}, 422),
    ("forward", {"user_id": "engineer", "to_user_id": "manager", "reason": " "}, 422),
    ("send-back", {"user_id": "engineer", "target_step_id": 99999, "reason": "Rework"}, 404),
    ("send-back", {"user_id": "engineer", "target_step_id": 0, "reason": "Rework"}, 422),
    ("send-back", {"user_id": "engineer", "target_step_id": 1, "reason": " "}, 422),
])
def test_rework_api_errors(client, operation, body, status):
    state = create_pending(client)
    client.post(f"/workflows/{state['id']}/start", json={"user_id": "submitter"})
    response = client.post(f"/workflows/{state['id']}/steps/{state['steps'][0]['id']}/{operation}", json=body)
    assert response.status_code == status


def test_api_can_use_a_five_step_python_definition(tmp_path):
    definition = WorkflowDefinition(
        name="Five approvals", version=1,
        steps=tuple(StepDefinition(name=f"Review {index}") for index in range(1, 6)),
    )
    with TestClient(create_app(f"sqlite:///{tmp_path / 'five.db'}", definition)) as client:
        assert client.get("/workflow-definition").json() == definition.model_dump(mode="json")
        submission = SUBMISSION | {"assignments": {step.name: "engineer" for step in definition.steps}}
        state = client.post("/workflows", json=submission).json()
        assert len(state["steps"]) == 5
        client.post(f"/workflows/{state['id']}/start", json={"user_id": "submitter"})
        for step in state["steps"]:
            response = client.post(f"/workflows/{state['id']}/steps/{step['id']}/approve", json={"user_id": "engineer"})
            assert response.status_code == 200
        assert response.json()["status"] == "Completed"