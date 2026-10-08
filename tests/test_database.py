import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from workflow_engine.models import WorkflowDefinition, WorkflowEvent, WorkflowInstance
from workflow_engine.database import initialize_database
from workflow_engine.engine import WorkflowEngine


def test_creates_the_five_domain_tables(sessions):
    assert set(inspect(sessions.kw["bind"]).get_table_names()) == {
        "workflow_definitions", "workflow_instances", "workflow_steps",
        "workflow_transitions", "workflow_events",
    }


@pytest.mark.parametrize("action", ["UPDATE workflow_events SET actor_id='changed'", "DELETE FROM workflow_events"])
def test_audit_events_cannot_be_changed(sessions, action):
    with sessions.begin() as session:
        definition = WorkflowDefinition(name="Example", version=1)
        session.add(definition)
        session.flush()
        instance = WorkflowInstance(workflow_definition_id=definition.id, entity_type="application", entity_id="1")
        session.add(instance)
        session.flush()
        session.add(WorkflowEvent(workflow_instance_id=instance.id, event_type="workflow_created", actor_id="owner"))
    with pytest.raises(IntegrityError, match="immutable"):
        with sessions.begin() as session:
            session.execute(text(action))


def test_legacy_original_assignment_backfill_is_idempotent(sessions):
    database = sessions.kw["bind"]
    with database.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE workflow_steps DROP COLUMN original_assigned_to")
        connection.exec_driver_sql("INSERT INTO workflow_definitions (id, name, version) VALUES (1, 'Legacy', 1)")
        connection.exec_driver_sql(
            "INSERT INTO workflow_instances (id, workflow_definition_id, entity_type, entity_id, status, created_at) "
            "VALUES (1, 1, 'app', 'legacy', 'RUNNING', '2026-10-08 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO workflow_steps (id, workflow_instance_id, name, step_type, status, \"order\", assigned_to) "
            "VALUES (1, 1, 'Approval', 'approval', 'ACTIVE', 1, 'john')"
        )
        connection.exec_driver_sql(
            "INSERT INTO workflow_events (workflow_instance_id, event_type, actor_id, created_at, metadata) "
            "VALUES (1, 'workflow_created', 'owner', '2026-10-08 00:00:00', '{}')"
        )
    initialize_database(database)
    engine = WorkflowEngine(sessions, recipient_is_eligible=lambda user: user == "sarah")
    assert engine.get_workflow_state(1).steps[0].original_assigned_to == "john"
    before = engine.get_events(1)
    engine.forward_approval(1, 1, "john", "sarah", "On leave")
    initialize_database(database)
    state = engine.get_workflow_state(1)
    assert state.steps[0].assigned_to == "sarah"
    assert state.steps[0].original_assigned_to == "john"
    assert engine.get_events(1)[:len(before)] == before
    with pytest.raises(IntegrityError, match="immutable"):
        with sessions.begin() as session:
            session.execute(text("DELETE FROM workflow_events"))