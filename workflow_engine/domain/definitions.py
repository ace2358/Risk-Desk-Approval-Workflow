from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from workflow_engine.domain.types import InvalidInput


class StepDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    step_type: str = "approval"


class WorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    steps: tuple[StepDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_steps(self):
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError("Step names must be unique")
        if any(step.step_type != "approval" for step in self.steps):
            raise ValueError("V0 only supports approval steps")
        return self

    def require_same_version(self, transitions: set[tuple[str, str, str]],
                             steps: list[tuple[str, str]] | None) -> None:
        expected_transitions = {(current.name, following.name, "always")
                                for current, following in zip(self.steps, self.steps[1:])}
        expected_steps = [(step.name, step.step_type) for step in self.steps]
        if transitions != expected_transitions or (steps is not None and steps != expected_steps):
            raise InvalidInput("Definition steps have changed; increment the workflow version")


technical_risk_workflow = WorkflowDefinition(
    name="Technical Risk Assessment", version=1,
    steps=(StepDefinition(name="Engineer Approval"), StepDefinition(name="Manager Approval"),
           StepDefinition(name="Final Approval")),
)


Nonblank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ApprovalStepDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Nonblank
    name: Nonblank
    order: int = Field(ge=1, strict=True)
    assigned_to: Nonblank


class ApprovalWorkflowDefinition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: Nonblank
    version: int = Field(ge=1, strict=True)
    steps: tuple[ApprovalStepDefinition, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sequence(self):
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("Step identifiers must be unique")
        if len({step.name for step in self.steps}) != len(self.steps):
            raise ValueError("Step names must be unique")
        if [step.order for step in self.steps] != list(range(1, len(self.steps) + 1)):
            raise ValueError("Steps must be supplied in contiguous sequence order starting at 1")
        return self

    def execution_definition(self) -> WorkflowDefinition:
        return WorkflowDefinition(
            name=self.name, version=self.version,
            steps=tuple(StepDefinition(name=step.name) for step in self.steps),
        )


class SavedApprovalDefinition(ApprovalWorkflowDefinition):
    id: int