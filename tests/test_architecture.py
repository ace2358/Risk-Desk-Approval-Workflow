import ast
import os
from pathlib import Path
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from workflow_engine.api import create_app
from workflow_engine.application.service import WorkflowService


PACKAGE = Path(__file__).resolve().parents[1] / "workflow_engine"


@pytest.mark.parametrize("layer,allowed", [
    ("domain", {"domain"}),
    ("application", {"domain", "application"}),
    ("infrastructure", {"domain", "application", "infrastructure"}),
    ("presentation", {"domain", "application", "presentation"}),
])
def test_layers_only_import_allowed_owners(layer, allowed):
    for source in (PACKAGE / layer).glob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            imports = []
            if isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"Use explicit owner imports in {source.name}"
                imports = [node.module]
            elif isinstance(node, ast.Import):
                imports = [alias.name for alias in node.names]
            for module in imports:
                if module and module.startswith("workflow_engine."):
                    assert module.split(".")[1] in allowed, f"{source.name} depends on {module}"
                if layer in {"domain", "application"}:
                    assert module.split(".")[0] not in {"sqlalchemy", "fastapi", "uvicorn"}


def test_inner_layers_and_agent_tools_import_without_database_or_http():
    code = """
import importlib.abc
import sys
class BlockFrameworks(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split('.')[0] in {'sqlalchemy', 'fastapi', 'uvicorn'}:
            raise AssertionError('Forbidden dependency: ' + fullname)
sys.meta_path.insert(0, BlockFrameworks())
from workflow_engine.domain.definitions import technical_risk_workflow
from workflow_engine.domain.workflow import WorkflowExecution
from workflow_engine.application.service import WorkflowService
from workflow_engine.agents import WorkflowTools, AgentContext
assert len(technical_risk_workflow.steps) == 3
assert AgentContext(workflow_instance_id=1, actor_id='owner').actor_id == 'owner'
"""
    subprocess.run([sys.executable, "-c", code], cwd=PACKAGE.parent, check=True)


def test_legacy_imports_are_same_owned_classes():
    from workflow_engine import database, definitions, models, schemas
    from workflow_engine.domain import definitions as domain_definitions, types
    from workflow_engine.engine import WorkflowEngine
    from workflow_engine.infrastructure import database as infrastructure_database, models as orm
    from workflow_engine.presentation import schemas as http

    assert definitions.WorkflowDefinition is domain_definitions.WorkflowDefinition
    assert database.initialize_database is infrastructure_database.initialize_database
    assert models.WorkflowStep is orm.WorkflowStep
    assert models.WorkflowStatus is types.WorkflowStatus
    assert schemas.EventState is http.EventState
    assert issubclass(WorkflowEngine, WorkflowService)


def test_entry_point_import_does_not_open_database(tmp_path):
    database = tmp_path / "not-created-on-import.db"
    environment = {**os.environ, "WORKFLOW_DATABASE_URL": f"sqlite:///{database}"}
    subprocess.run([sys.executable, "-c", "from workflow_engine.api import app; assert app.title"],
                   cwd=PACKAGE.parent, env=environment, check=True)
    assert not database.exists()


def test_environment_wiring_and_static_assets_survive_restart(tmp_path, monkeypatch):
    database = tmp_path / "configured.db"
    monkeypatch.setenv("WORKFLOW_DATABASE_URL", f"sqlite:///{database}")
    with TestClient(create_app()) as client:
        assert isinstance(client.app.state.workflow_engine, WorkflowService)
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/workflow-mark.svg").status_code == 200
        response = client.post("/workflows", json={
            "entity_type": "application", "entity_id": "layered-startup", "user_id": "submitter",
            "assignments": {"Engineer Approval": "engineer", "Manager Approval": "manager", "Final Approval": "final"},
        })
        assert response.status_code == 201
        state = response.json()
    assert database.exists()
    with TestClient(create_app()) as client:
        assert client.get(f"/workflows/{state['id']}").json() == state
        assert len(client.get(f"/workflows/{state['id']}/events").json()) == 1