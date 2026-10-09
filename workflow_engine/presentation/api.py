from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from workflow_engine.application.service import WorkflowService
from workflow_engine.domain.definitions import WorkflowDefinition
from workflow_engine.domain.types import InvalidInput, InvalidTransition, NotFound, PermissionDenied, WorkflowError
from workflow_engine.presentation.demo import MOCK_USERS
from workflow_engine.presentation.schemas import (
    ActorRequest, CreateWorkflowRequest, EventState, ForwardApprovalRequest, MockUser,
    RejectionRequest, SendBackRequest, WorkflowState,
)


def service(request: Request) -> WorkflowService:
    return request.app.state.workflow_engine


def create_http_app(definition: WorkflowDefinition, lifespan) -> FastAPI:
    app = FastAPI(title="Experimental Workflow Engine", version="0.1.0", lifespan=lifespan)
    static_directory = Path(__file__).resolve().parent.parent / "static"
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
        return service(request).list_workflows()

    @app.post("/workflows", response_model=WorkflowState, status_code=201)
    def create_workflow(body: CreateWorkflowRequest, request: Request):
        return service(request).create_workflow(**body.model_dump())

    @app.post("/workflows/{instance_id}/start", response_model=WorkflowState)
    def start_workflow(instance_id: int, body: ActorRequest, request: Request):
        return service(request).start_workflow(instance_id, body.user_id)

    @app.get("/workflows/{instance_id}", response_model=WorkflowState)
    def get_workflow(instance_id: int, request: Request):
        return service(request).get_workflow_state(instance_id)

    @app.post("/workflows/{instance_id}/steps/{step_id}/approve", response_model=WorkflowState)
    def approve_step(instance_id: int, step_id: int, body: ActorRequest, request: Request):
        return service(request).approve_step(instance_id, step_id, body.user_id)

    @app.post("/workflows/{instance_id}/steps/{step_id}/reject", response_model=WorkflowState)
    def reject_step(instance_id: int, step_id: int, body: RejectionRequest, request: Request):
        return service(request).reject_step(instance_id, step_id, body.user_id, body.reason)

    @app.post("/workflows/{instance_id}/steps/{step_id}/send-back", response_model=WorkflowState)
    def send_back_step(instance_id: int, step_id: int, body: SendBackRequest, request: Request):
        return service(request).send_back_step(instance_id, step_id, body.target_step_id, body.user_id, body.reason)

    @app.post("/workflows/{instance_id}/steps/{step_id}/forward", response_model=WorkflowState)
    def forward_approval(instance_id: int, step_id: int, body: ForwardApprovalRequest, request: Request):
        return service(request).forward_approval(instance_id, step_id, body.user_id, body.to_user_id, body.reason)

    @app.post("/workflows/{instance_id}/cancel", response_model=WorkflowState)
    def cancel_workflow(instance_id: int, body: ActorRequest, request: Request):
        return service(request).cancel_workflow(instance_id, body.user_id)

    @app.get("/workflows/{instance_id}/events", response_model=list[EventState])
    def get_events(instance_id: int, request: Request):
        return service(request).get_events(instance_id)

    return app