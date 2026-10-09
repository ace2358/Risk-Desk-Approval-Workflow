"""Composition root: configuration and concrete adapter wiring."""

import os
from contextlib import asynccontextmanager

from workflow_engine.application.service import WorkflowService
from workflow_engine.domain.definitions import WorkflowDefinition, technical_risk_workflow
from workflow_engine.infrastructure.database import create_database, initialize_database, session_factory
from workflow_engine.infrastructure.repository import SQLAlchemyUnitOfWork
from workflow_engine.presentation.api import create_http_app
from workflow_engine.presentation.demo import MOCK_USERS


def create_app(database_url: str | None = None, definition: WorkflowDefinition = technical_risk_workflow):
    @asynccontextmanager
    async def lifespan(app):
        database = create_database(database_url or os.getenv("WORKFLOW_DATABASE_URL", "sqlite:///./workflow.db"))
        try:
            initialize_database(database)
            sessions = session_factory(database)
            app.state.workflow_engine = WorkflowService(
                lambda write: SQLAlchemyUnitOfWork(sessions, write), definition,
                recipient_is_eligible=lambda user_id: any(user.id == user_id for user in MOCK_USERS),
            )
            yield
        finally:
            database.dispose()

    return create_http_app(definition, lifespan)