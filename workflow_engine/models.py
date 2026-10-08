from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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


class Base(DeclarativeBase):
    pass


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"
    __table_args__ = (UniqueConstraint("name", "version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String)
    version: Mapped[int]


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_definition_id: Mapped[int] = mapped_column(ForeignKey("workflow_definitions.id"))
    entity_type: Mapped[str] = mapped_column(String)
    entity_id: Mapped[str] = mapped_column(String)
    status: Mapped[WorkflowStatus] = mapped_column(
        Enum(WorkflowStatus, native_enum=False, create_constraint=True),
        default=WorkflowStatus.PENDING,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)


class WorkflowStep(Base):
    __tablename__ = "workflow_steps"
    __table_args__ = (
        UniqueConstraint("workflow_instance_id", "order"),
        UniqueConstraint("workflow_instance_id", "name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_instance_id: Mapped[int] = mapped_column(ForeignKey("workflow_instances.id"), index=True)
    name: Mapped[str] = mapped_column(String)
    step_type: Mapped[str] = mapped_column(String)
    status: Mapped[StepStatus] = mapped_column(
        Enum(StepStatus, native_enum=False, create_constraint=True), default=StepStatus.PENDING
    )
    order: Mapped[int]
    assigned_to: Mapped[str] = mapped_column(String)
    original_assigned_to: Mapped[str] = mapped_column(String)


class WorkflowTransition(Base):
    __tablename__ = "workflow_transitions"
    __table_args__ = (UniqueConstraint("workflow_definition_id", "from_step"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_definition_id: Mapped[int] = mapped_column(ForeignKey("workflow_definitions.id"))
    from_step: Mapped[str] = mapped_column(String)
    to_step: Mapped[str] = mapped_column(String)
    condition: Mapped[str] = mapped_column(String, default="always")


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_instance_id: Mapped[int] = mapped_column(ForeignKey("workflow_instances.id"), index=True)
    step_id: Mapped[int | None] = mapped_column(ForeignKey("workflow_steps.id"))
    event_type: Mapped[str] = mapped_column(String)
    actor_id: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now)
    event_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, default=dict)