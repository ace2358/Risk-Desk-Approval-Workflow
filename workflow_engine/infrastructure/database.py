from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker

from workflow_engine.infrastructure.models import Base


def create_database(url: str = "sqlite:///./workflow.db") -> Engine:
    engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 10})

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def initialize_database(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        columns = {column["name"] for column in inspect(connection).get_columns("workflow_steps")}
        if "original_assigned_to" not in columns:
            connection.exec_driver_sql(
                "ALTER TABLE workflow_steps ADD COLUMN original_assigned_to VARCHAR NOT NULL DEFAULT ''"
            )
            connection.exec_driver_sql("UPDATE workflow_steps SET original_assigned_to = assigned_to")
        for action in ("UPDATE", "DELETE"):
            connection.exec_driver_sql(
                f"CREATE TRIGGER IF NOT EXISTS workflow_events_no_{action.lower()} "
                f"BEFORE {action} ON workflow_events BEGIN "
                "SELECT RAISE(ABORT, 'Workflow events are immutable'); END"
            )


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)