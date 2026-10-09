# Experimental Agentic Workflow Engine

A small Python 3.12+ prototype that keeps workflow state and permissions deterministic.
It includes a small local browser workbench and mock users. There is no LLM integration,
authentication, or external service dependency.

## Documentation

Read these guides in order for the system design and its current behavior:

| Guide | Covers |
| --- | --- |
| [Architecture](docs/architecture.md) | Components, dependency boundaries, transactions, startup, and future agent tools |
| [Database design](docs/database-design.md) | ER diagram, all five tables, field types, constraints, indexes, and audit protection |
| [Workflow flows](docs/workflow-flows.md) | State diagrams, approval sequence, rejection/cancellation, permissions, and audit events |
| [Refactor record](docs/refactor.md) | Final layout, compatibility decisions, changed files, and verification |

The guides describe implemented sequential approval, send-back, and forwarding behavior.
Local setup and runnable examples remain below.

## Architecture

```text
FastAPI -> WorkflowService -> WorkflowExecution (domain rules)
            |
        repository / unit-of-work ports
            ^
        SQLAlchemy adapter -> SQLite

WorkflowTools -> injected application readers
bootstrap.py wires concrete adapters at startup
```

- [domain/workflow.py](workflow_engine/domain/workflow.py): execution entities, permissions, transitions, and events.
- [domain/definitions.py](workflow_engine/domain/definitions.py): reusable definitions and version invariants.
- [application/service.py](workflow_engine/application/service.py): use cases through abstract persistence ports.
- [application/contracts.py](workflow_engine/application/contracts.py): commands and detached snapshots.
- [infrastructure/repository.py](workflow_engine/infrastructure/repository.py): ORM mapping and transactional persistence.
- [presentation/api.py](workflow_engine/presentation/api.py): routes, static serving, and HTTP errors.
- [bootstrap.py](workflow_engine/bootstrap.py): configuration and concrete dependency wiring.
- [agents.py](workflow_engine/agents.py): an inactive agent contract and explicit tool boundary.
- [tests](tests): database, engine, API, and tool-boundary behavior tests.

The root engine, database, models, definitions, schemas, demo, and API modules remain
compatibility facades. `WorkflowEngine(sessions, ...)` delegates to the application service;
`workflow_engine.api:app` remains the entry point. New business code uses the owning layers.
Domain and application have no SQLAlchemy/FastAPI dependency; existing Pydantic definition
validation is retained. No dependencies or schema changes were added. API handlers and
future tools call use cases rather than modifying ORM models themselves.

## Local Setup

### Running Container

The app is running at http://127.0.0.1:8000 in the Docker container `risk-desk-local`.
Docker Desktop was started successfully. The normal Python image build still cannot
download packages because of local DNS/proxy failures, so this launch uses
`prefecthq/prefect:3-python3.12` as a temporary preinstalled Python runtime. Only our
Uvicorn app runs; no Prefect service or integration is enabled.

The project directory is mounted read-only at `/app`, and SQLite is stored in the named
volume `risk-desk-data` at `/data/workflow.db`. No native virtual-environment installation
is needed for this running container. The port is bound to loopback only.

```powershell
docker --context default stop risk-desk-local
docker --context default start risk-desk-local
docker --context default logs --tail 30 risk-desk-local
```

Stop and start are separate commands; use the one you need. Data survives both. Do not
delete the data volume unless intentionally resetting your assessments. Python source
changes need a container restart; the running container does not use auto-reload.

For a clean, smaller image once package downloads work, [Dockerfile](Dockerfile) supplies
a normal runtime target and a separate test target:

```powershell
docker --context default build --builder default --load --target test -t risk-desk-tests:local .
docker --context default run --rm risk-desk-tests:local
docker --context default build --builder default --load --target app -t risk-desk:local .
```

These builds have not succeeded yet because the package-index networking problem remains.
They are not the image used by the currently running container. The normal runtime target
runs as a non-root user; the prebuilt fallback is a local-only development workaround.

### Native Python Setup

Run from this directory in PowerShell. Activation is not necessary.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m uvicorn workflow_engine.api:app --reload --host 127.0.0.1 --port 8000
```

After starting the server, open http://127.0.0.1:8000 for the Risk Desk workbench.
Use http://127.0.0.1:8000/docs for the interactive API. The UI and API run in the same
process; no Node.js, frontend build, CDN, or second server is required. VS Code also has
a **Run local Risk Desk** task after dependencies are installed. If port 8000 is occupied,
use `--port 8001` and open http://127.0.0.1:8001 instead.
SQLite creates `workflow.db` in the current working directory on startup.
For a different SQLite file, set `WORKFLOW_DATABASE_URL`, for example:

```powershell
$env:WORKFLOW_DATABASE_URL = 'sqlite:///./experiment.db'
```

Dependencies are FastAPI, Pydantic, SQLAlchemy, and Uvicorn. Tests additionally use pytest
and HTTPX (FastAPI's test client). No API key is required.

### Verification Status

During initial project creation, Python 3.12 was available and all source/test files passed
`compileall`. Package installation failed in the development environment, so pytest and
server startup could not be verified. Run the setup commands above in an environment with
access to the required packages before treating this prototype as behavior-verified.

The local UI initially passed browser tests using temporary API fixtures for completion,
rejection, cancellation, observer controls, audit rendering, and refresh recovery. Desktop
and mobile layouts were checked at 1440px, 390px, and 320px.

The Docker fallback subsequently passed a **real browser approval chain** through Completed
with nine committed audit events, and isolated FastAPI/SQLite smoke checks for creation,
starting, approval, completion, rejection, cancellation, permissions, invalid states, audit
events, static assets, and browsing. The native launch task still lacks `uvicorn`, and
the native test command remains blocked by missing dependencies. The complete suite now
passes: **104 tests** after the architectural refactor (87 passed before it) in an isolated
container with offline pytest 7.4.4 tooling and the
fallback runtime libraries. This compatibility run does not verify the declared pytest
8+ dependency range. One upstream FastAPI TestClient/HTTPX deprecation warning remains.

If installation cannot reach the package index, restore network access or use an approved
package mirror. To install without network access, obtain a complete compatible wheel set
on a connected Python 3.12 machine, then install locally:

```powershell
.\.venv\Scripts\python.exe -m pip install --no-index --find-links C:\path\to\wheels "setuptools>=69" wheel
.\.venv\Scripts\python.exe -m pip install --no-index --find-links C:\path\to\wheels --no-build-isolation -e ".[dev]"
```

The wheel set must include setuptools, wheel, and all runtime/test dependencies, including their
transitive dependencies. Do not start the workflow requests until Uvicorn reports startup
complete; connection failures against port 8000 usually mean the server is not running.

## Local Workbench And Mock Users

The browser workbench provides an assessment list with search and status filtering,
definition-driven reviewer assignments, ordered approval steps, send-back/forward dialogs,
rejection/cancellation dialogs,
and an audit history view. Switching the selected mock user changes the `user_id` sent
to the existing API; it never bypasses engine validation.

| Name | ID | Default role |
| --- | --- | --- |
| Jordan Lee | `submitter` | Submitter |
| Sam Rivera | `engineer` | Engineer |
| Morgan Chen | `manager` | Manager |
| Alex Patel | `final` | Final reviewer |
| Taylor Quinn | `observer` | Observer |

The directory is defined in [demo.py](workflow_engine/demo.py), not in user database tables.
Roles are display labels, not grants. Any selected identity can submit a new assessment,
and any identity can be explicitly assigned as a reviewer. Taylor has no assignment by
default, making that identity useful for checking permission-denied behavior.

1. Select Jordan Lee and create an assessment, keeping the default reviewers.
2. Start the workflow as Jordan.
3. Switch to Sam Rivera and approve Engineer Approval.
4. Switch to Morgan Chen and approve Manager Approval.
5. Switch to Alex Patel and approve Final Approval.
6. Open Audit trail to inspect the nine committed events.

For rejection, create another assessment and reject its active step as its assigned reviewer.
For cancellation, select that assessment's submitter. Other users' action buttons are
disabled with permission tooltips; direct API calls still receive engine permission checks.
Use Refresh to load changes made elsewhere. There is no live polling or push connection.

## Sequential Rework And Forwarding

Definitions accept one or more approval steps, not a fixed three. Configure them in Python
with `WorkflowDefinition`; pass the definition to `WorkflowEngine` or
`create_app(definition=...)`. Increment its version when changing steps. Reusing an existing
name/version with changed steps or transitions is rejected. The bundled default remains
Technical Risk Assessment version 1; no designer or definition-write API was added.

Only the current active assignee can send a step back or forward it. Send-back requires
an approved earlier step reachable through this instance's stored sequential definition.
The target becomes Active; target and downstream approvals are invalidated in current
state while their original audit events remain. Earlier approvals and all assignments
are retained. Rework can repeat without an arbitrary cycle limit; terminal workflows
cannot be reopened.

Forwarding keeps the step Active and the workflow Running. It changes `assigned_to`,
preserves `original_assigned_to`, and grants approval to the recipient rather than the
former assignee. The engine requires an explicit `recipient_is_eligible` callback;
without one, forwarding fails closed. The API permits existing mock identities, including
the observer; display roles are not grants. Both operations require nonblank reasons.

| API operation | JSON body |
| --- | --- |
| `POST /workflows/{id}/steps/{step_id}/send-back` | `{"user_id":"final","target_step_id":1,"reason":"Recheck mitigation"}` |
| `POST /workflows/{id}/steps/{step_id}/forward` | `{"user_id":"manager","to_user_id":"observer","reason":"Reviewer on leave"}` |
| `GET /workflow-definition` | Read-only name, version, and ordered step specification |

Step IDs above are illustrative; use IDs returned for the same instance. The UI offers
the two actions on authorized active steps and shows original/current assignees separately.
Startup adds and backfills the original-assignee column for existing SQLite files without
deleting instances or audit history. It is an explicit additive migration, not a general
migration framework.

[examples/rework.py](examples/rework.py) runs **Engineer → Manager → Quality → Safety → Final**
in a temporary database, sends Final back to Engineer, forwards Manager from John to Sarah,
and completes with the full event history. Run after installing dependencies:

```powershell
.\.venv\Scripts\python.exe -m examples.rework
```

Mock users are available automatically, but no sample workflows are inserted on startup.
Assessment data persists in SQLite across restarts; the selected user is not a login.
The browser renders input as text and uses the same-origin API. All static assets are
bundled under [workflow_engine/static](workflow_engine/static).

## Initial Workflow

```text
Application submitted -> Engineer Approval -> Manager Approval -> Final Approval -> Completed
```

Submission creates a **Pending** workflow with three **Pending** approval steps.
Starting it sets the workflow to **Running** and activates only Engineer Approval.
The application itself lives outside this engine; `entity_type` and `entity_id` are references,
not an application database. Definitions describe sequential approval steps in Python;
their name/version and unconditional transitions are persisted when first used.

Change the definition version whenever changing its steps. Existing instances retain their
stored steps and transitions. V0 has no definition editor or arbitrary graph configuration.
A later JSON loader can produce the same Pydantic definition specification.

## Permissions And States

- Creation requires exactly one nonblank assignee for each defined step.
- Only the submitter can start or cancel an instance. The immutable creation event records the submitter.
- Only a step's exact assignee can approve or reject it, and only while it is Active in a Running workflow.
- An approval activates the next Pending step. The last approval completes the workflow.
- A rejection requires a nonblank reason, rejects the workflow, and skips unfinished steps.
- Cancellation is allowed only from Pending or Running and skips unfinished steps.
- Approved steps remain Approved when a later step rejects or the workflow is cancelled.
- Completed, Rejected, and Cancelled workflows cannot transition again.
- `completed_at` records termination for all three terminal states. Timestamps are stored and returned as naive UTC.

**These checks are not authentication.** V0 trusts the caller-supplied `user_id`; callers can
impersonate an assignee. Reads are unrestricted. Keep the server local and do not use it
with real sensitive business data. Later authentication should supply verified identities
without moving permission checks out of the engine.

## API

| Method | Path | Body |
| --- | --- | --- |
| GET | `/` | Browser workbench |
| GET | `/demo/users` | Fixed mock-user directory |
| GET | `/workflows` | None; all instances, newest first |
| POST | `/workflows` | `entity_type`, `entity_id`, `user_id`, `assignments` |
| POST | `/workflows/{id}/start` | `user_id` |
| GET | `/workflows/{id}` | None |
| POST | `/workflows/{id}/steps/{step_id}/approve` | `user_id` |
| POST | `/workflows/{id}/steps/{step_id}/reject` | `user_id`, `reason` |
| POST | `/workflows/{id}/cancel` | `user_id` |
| GET | `/workflows/{id}/events` | None |

Creation returns HTTP 201. Missing instances or steps return 404, permission failures 403,
invalid transitions 409, and invalid input 422. Unknown request fields are rejected.
Workflow responses include ordered steps; event responses expose the JSON field `metadata`.
Internally it is `event_metadata` because SQLAlchemy reserves the attribute `metadata`.

### Try A Complete Workflow

Start the server first, then run:

```powershell
$base = 'http://127.0.0.1:8000'
$submission = @{
    entity_type = 'application'
    entity_id = 'app-123'
    user_id = 'submitter'
    assignments = @{
        'Engineer Approval' = 'engineer'
        'Manager Approval' = 'manager'
        'Final Approval' = 'final'
    }
} | ConvertTo-Json

$workflow = Invoke-RestMethod -Method Post -Uri "$base/workflows" -ContentType 'application/json' -Body $submission
$id = $workflow.id
$workflow = Invoke-RestMethod -Method Post -Uri "$base/workflows/$id/start" -ContentType 'application/json' -Body '{"user_id":"submitter"}'

foreach ($step in $workflow.steps) {
    $actor = @{ user_id = $step.assigned_to } | ConvertTo-Json
    $workflow = Invoke-RestMethod -Method Post -Uri "$base/workflows/$id/steps/$($step.id)/approve" -ContentType 'application/json' -Body $actor
}

$workflow
Invoke-RestMethod -Uri "$base/workflows/$id/events"
```

## Transactions And Audit

Each engine mutation owns a fresh session and a single transaction. SQLite `BEGIN IMMEDIATE`
reserves the writer before reading current state, serializing competing actions. A duplicate
approval therefore observes the already-approved step and fails instead of advancing twice.
This deliberately favors clarity over write throughput for the local prototype.

All step changes, workflow changes, and corresponding events commit together. A failed
transition rolls back everything, including the approval event. Read operations use explicit
read transactions so their multi-query snapshots remain consistent; closing the session
ends the read transaction. Sessions are not shared between requests.

Events are ordered by ID, not timestamps. They record creation, start, activation, approval,
rejection, skipped steps, completion, and cancellation. SQLite triggers refuse updates and
deletes even through direct SQL. These are append-only application records, not protection
against someone who controls the database file or can remove the triggers.

## Future Agents

`WorkflowAgent` is abstract and is not invoked by the engine or API. `WorkflowTools` accepts
read callbacks, not a database or session. For example, `WorkflowTools(engine.get_workflow_state)`
enables workflow snapshots. Application and role providers are optional injected callbacks;
they are not fake integrations. All returned objects are copies.

The conceptual tools are:

- `get_application`: optional external application reader.
- `get_workflow_state`: deterministic engine snapshot.
- `get_available_roles`: optional external roles reader.
- `add_approval`: unavailable until a deterministic reviewer-addition operation exists.
- `request_information`: unavailable in V0.
- `escalate_workflow`: unavailable in V0.

Unavailable tools raise `ToolUnavailable`; they never report success without doing work.
This is a code architecture boundary, not a Python security sandbox for untrusted plugins.
Future mutation tools must bind a trusted actor identity and call permission-checked engine
operations. Do not give an LLM SQL access, session access, or generic arbitrary-code tools.

## Incremental Development

V0 adds package/persistence, then the engine and behavior tests, then HTTP endpoints and
their tests. The agent abstraction is an inactive contract only; enable no reasoning loop
until the deterministic tests pass. Tests cover all ten requested engine behaviors plus
terminal-state restrictions, transaction rollback, concurrent approvals, persistence,
API errors, and the tool boundary.

Next, verify the suite locally, then add one narrowly scoped external read provider.
Conditional transitions, parallel approvals, dynamic reviewers, and actual LLM integration
remain future work. There is no authentication, Kubernetes, PostgreSQL, OAuth,
message queue, microservice layer, or visual workflow designer.