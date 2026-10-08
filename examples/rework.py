from tempfile import TemporaryDirectory
from pathlib import Path

from workflow_engine.database import create_database, initialize_database, session_factory
from workflow_engine.definitions import StepDefinition, WorkflowDefinition
from workflow_engine.engine import WorkflowEngine
from workflow_engine.models import StepStatus, WorkflowStatus


def main():
    definition = WorkflowDefinition(
        name="Five-stage risk review", version=1,
        steps=tuple(StepDefinition(name=name) for name in (
            "Engineer", "Manager", "Quality", "Safety", "Final",
        )),
    )
    users = {"engineer", "john", "quality", "safety", "final", "sarah"}
    with TemporaryDirectory() as directory:
        database = create_database(f"sqlite:///{Path(directory) / 'example.db'}")
        sessions = session_factory(database)
        try:
            initialize_database(database)
            engine = WorkflowEngine(sessions, definition, recipient_is_eligible=users.__contains__)
            state = engine.create_workflow("application", "example-5-step", dict(zip(
                (step.name for step in definition.steps),
                ("engineer", "john", "quality", "safety", "final"),
            )), "owner")
            state = engine.start_workflow(state.id, "owner")
            for step in state.steps[:-1]:
                state = engine.approve_step(state.id, step.id, step.assigned_to)
            state = engine.send_back_step(
                state.id, state.steps[-1].id, state.steps[0].id, "final", "Recheck mitigation",
            )
            state = engine.approve_step(state.id, state.steps[0].id, "engineer")
            state = engine.forward_approval(
                state.id, state.steps[1].id, "john", "sarah", "John is on leave",
            )
            assert state.steps[1].original_assigned_to == "john"
            assert state.steps[1].status == StepStatus.ACTIVE
            for step in state.steps[1:]:
                state = engine.approve_step(state.id, step.id, step.assigned_to)
            assert state.status == WorkflowStatus.COMPLETED
            print(f"{definition.name}: {state.status.value}")
            for event in engine.get_events(state.id):
                print(event.id, event.event_type, event.actor_id, event.metadata)
        finally:
            database.dispose()


if __name__ == "__main__":
    main()