from pydantic import BaseModel, ConfigDict, Field, model_validator


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


technical_risk_workflow = WorkflowDefinition(
    name="Technical Risk Assessment",
    version=1,
    steps=(
        StepDefinition(name="Engineer Approval"),
        StepDefinition(name="Manager Approval"),
        StepDefinition(name="Final Approval"),
    ),
)