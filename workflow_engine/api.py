import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from workflow_engine.database import create_database, initialize_database, session_factory
from workflow_engine.demo import MOCK_USERS
from workflow_engine.definitions import WorkflowDefinition, technical_risk_workflow
from workflow_engine.engine import (
    InvalidInput, InvalidTransition, NotFound, PermissionDenied, WorkflowEngine, WorkflowError,
)
from workflow_engine.schemas import (
    ActorRequest, CreateWorkflowRequest, EventState, ForwardApprovalRequest, MockUser,
    RejectionRequest, SendBackRequest, WorkflowState,
)


def create_app(
    database_url: str | None = None, definition: WorkflowDefinition = technical_risk_workflow,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database = create_database(database_url or os.getenv("WORKFLOW_DATABASE_URL", "sqlite:///./workflow.db"))
        try:
            initialize_database(database)
            app.state.workflow_engine = WorkflowEngine(
                session_factory(database), definition,
                recipient_is_eligible=lambda user_id: any(user.id == user_id for user in MOCK_USERS),
            )
            yield
        finally:
            database.dispose()

    app = FastAPI(title="Experimental Workflow Engine", version="0.1.0", lifespan=lifespan)
    static_directory = Path(__file__).with_name("static")
    app.mount("/static", StaticFiles(directory=static_directory), name="static")

    @app.get("/", include_in_schema=False)
    def workbench():
        return FileResponse(static_directory / "index.html")

    @app.exception_handler(WorkflowError)
    async def workflow_error_handler(_request: Request, error: WorkflowError):
        status_codes = {NotFound: 404, PermissionDenied: 403, InvalidTransition: 409, InvalidInput: 422}
        return JSONResponse(status_code=status_codes.get(type(error), 400), content={"detail": str(error)})

    @app.get("/demo/users", response_model=list[MockUser])
    def get_mock_users():
        return MOCK_USERS

    @app.get("/workflow-definition", response_model=WorkflowDefinition)
    def get_workflow_definition():
        return definition

    @app.get("/workflows", response_model=list[WorkflowState])
    def list_workflows(request: Request):
        return request.app.state.workflow_engine.list_workflows()

    @app.post("/workflows", response_model=WorkflowState, status_code=201)
    def create_workflow(body: CreateWorkflowRequest, request: Request):
        return request.app.state.workflow_engine.create_workflow(**body.model_dump())

    @app.post("/workflows/{instance_id}/start", response_model=WorkflowState)
    def start_workflow(instance_id: int, body: ActorRequest, request: Request):
        return request.app.state.workflow_engine.start_workflow(instance_id, body.user_id)

    @app.get("/workflows/{instance_id}", response_model=WorkflowState)
    def get_workflow(instance_id: int, request: Request):
        return request.app.state.workflow_engine.get_workflow_state(instance_id)

    @app.post("/workflows/{instance_id}/steps/{step_id}/approve", response_model=WorkflowState)
    def approve_step(instance_id: int, step_id: int, body: ActorRequest, request: Request):
        return request.app.state.workflow_engine.approve_step(instance_id, step_id, body.user_id)

    @app.post("/workflows/{instance_id}/steps/{step_id}/reject", response_model=WorkflowState)
    def reject_step(instance_id: int, step_id: int, body: RejectionRequest, request: Request):
        return request.app.state.workflow_engine.reject_step(instance_id, step_id, body.user_id, body.reason)

    @app.post("/workflows/{instance_id}/steps/{step_id}/send-back", response_model=WorkflowState)
    def send_back_step(instance_id: int, step_id: int, body: SendBackRequest, request: Request):
        return request.app.state.workflow_engine.send_back_step(
            instance_id, step_id, body.target_step_id, body.user_id, body.reason,
        )

    @app.post("/workflows/{instance_id}/steps/{step_id}/forward", response_model=WorkflowState)
    def forward_approval(instance_id: int, step_id: int, body: ForwardApprovalRequest, request: Request):
        return request.app.state.workflow_engine.forward_approval(
            instance_id, step_id, body.user_id, body.to_user_id, body.reason,
        )

    @app.post("/workflows/{instance_id}/cancel", response_model=WorkflowState)
    def cancel_workflow(instance_id: int, body: ActorRequest, request: Request):
        return request.app.state.workflow_engine.cancel_workflow(instance_id, body.user_id)

    @app.get("/workflows/{instance_id}/events", response_model=list[EventState])
    def get_events(instance_id: int, request: Request):
        return request.app.state.workflow_engine.get_events(instance_id)

    return app


app = create_app()