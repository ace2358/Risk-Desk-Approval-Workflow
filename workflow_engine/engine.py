"""Legacy session-based constructor; new callers inject a UoW into WorkflowService."""

from collections.abc import Callable

from sqlalchemy.orm import Session, sessionmaker

from workflow_engine.application.service import WorkflowService
from workflow_engine.domain.definitions import WorkflowDefinition, technical_risk_workflow
from workflow_engine.domain.types import InvalidInput, InvalidTransition, NotFound, PermissionDenied, WorkflowError
from workflow_engine.infrastructure.repository import SQLAlchemyUnitOfWork


class WorkflowEngine(WorkflowService):
    def __init__(
        self, sessions: sessionmaker[Session], definition: WorkflowDefinition = technical_risk_workflow,
        *, recipient_is_eligible: Callable[[str], bool] | None = None,
    ):
        self.sessions = sessions
        super().__init__(
            lambda write: SQLAlchemyUnitOfWork(sessions, write), definition,
            recipient_is_eligible=recipient_is_eligible,
        )


__all__ = ["WorkflowEngine", "WorkflowError", "InvalidInput", "InvalidTransition", "NotFound", "PermissionDenied"]