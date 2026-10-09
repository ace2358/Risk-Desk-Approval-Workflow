# Project Conventions

- Keep this Python 3.12+ prototype small, readable, and incremental.
- Keep transitions, permissions, and audit intents in domain WorkflowExecution; orchestrate use cases in WorkflowService.
- Keep domain/application independent of FastAPI and SQLAlchemy; wire adapters in bootstrap.py.
- Commit state and audit events in the same infrastructure unit-of-work transaction.
- Keep FastAPI routes thin and use Pydantic request/response models.
- Define workflows in Python and increment their version when changing steps.
- Agents receive explicit tools, never database sessions or arbitrary SQL access.
- Do not add LLM integration, authentication, or unnecessary infrastructure to V0.
- Docker is permitted for the requested local run; keep the API and UI in one container and persist SQLite in a volume.
- Keep the requested local UI as bundled HTML/CSS/JavaScript served by FastAPI, without a frontend build or external assets.
- Mock users are demo identities only; permissions remain enforced by WorkflowEngine.
- Add behavior-focused pytest tests for changed workflow rules.
- Run `.\.venv\Scripts\python.exe -m pytest` from the project root.
- See README.md for local setup and the initial dependency-installation verification blocker.