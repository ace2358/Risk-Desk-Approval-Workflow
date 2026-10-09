from datetime import UTC, datetime
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class WorkflowStatus(StrEnum):
    PENDING = "Pending"
    RUNNING = "Running"
    COMPLETED = "Completed"
    REJECTED = "Rejected"
    CANCELLED = "Cancelled"


class StepStatus(StrEnum):
    PENDING = "Pending"
    ACTIVE = "Active"
    APPROVED = "Approved"
    REJECTED = "Rejected"
    SKIPPED = "Skipped"


class WorkflowError(Exception):
    pass


class NotFound(WorkflowError):
    pass


class InvalidTransition(WorkflowError):
    pass


class PermissionDenied(WorkflowError):
    pass


class InvalidInput(WorkflowError):
    pass