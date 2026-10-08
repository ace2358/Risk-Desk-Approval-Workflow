# Architecture

[Database design](database-design.md) | [Workflow flows](workflow-flows.md) |
[Project overview and setup](../README.md)

## Purpose And Scope

V0 proves a small deterministic workflow engine before adding AI reasoning. The running
system is a single Python process using FastAPI, Pydantic, SQLAlchemy, and SQLite. There
are no workers, queues, remote services, or LLM calls.

The engine is the authority for state and permission decisions. A future agent may
interpret an application or recommend actions, but cannot make an otherwise illegal
transition legal.

## Component Map

```mermaid
flowchart TD
  Browser[Local browser workbench] --> API
    Client[Local API client] --> API[FastAPI routes]
  API --> Static[Bundled HTML, CSS, and JavaScript]
    API --> Schemas[Pydantic request validation]
    API --> Engine[Deterministic WorkflowEngine]
    Definition[Python workflow definition] --> Engine
    Engine --> ORM[SQLAlchemy models and sessions]
    ORM --> DB[(SQLite state and audit events)]
    Engine --> Snapshot[Pydantic state snapshots]
    Snapshot --> API
    Agent[Future WorkflowAgent] -. explicit calls .-> Tools[WorkflowTools]
    Tools -. workflow reads .-> Engine
    Tools -. optional reader callbacks .-> External[External applications and roles]
```

Solid arrows describe the active request path. Dashed arrows describe the optional tool
boundary; there is no enabled agent loop or external provider in the API.

## Responsibilities

| Component | Owns | Does not own |
| --- | --- | --- |
| [api.py](../workflow_engine/api.py) | Routes, application lifespan, HTTP error mapping | Approval rules or database writes |
| [static/app.js](../workflow_engine/static/app.js) | Browser state, user selection, action forms, API requests, audit rendering | Authoritative permissions or persisted workflow state |
| [demo.py](../workflow_engine/demo.py) | Fixed names, IDs, and display roles for local mock users | Authentication, user storage, or permission grants |
| [schemas.py](../workflow_engine/schemas.py) | Request shape, nonblank identifiers, response snapshots | Legal state transitions |
| [engine.py](../workflow_engine/engine.py) | State changes, permissions, transition selection, audit events, transaction boundaries | Application classification or LLM reasoning |
| [definitions.py](../workflow_engine/definitions.py) | Versioned sequential approval specifications in Python | A visual designer or dynamic reviewer insertion |
| [models.py](../workflow_engine/models.py) | Five persisted concepts and database constraints | Workflow orchestration |
| [database.py](../workflow_engine/database.py) | Engine/session factories, schema initialization, SQLite foreign keys and audit triggers | Business permissions |
| [agents.py](../workflow_engine/agents.py) | Abstract agent interface and explicit reader tools | Database sessions or an implemented reasoning loop |

The engine uses Pydantic models directly for input validation and detached output. There
is deliberately no repository/service hierarchy: the small engine queries SQLAlchemy
inside its own transactions.

## One Action, One Transaction

```mermaid
sequenceDiagram
    actor Caller
    participant API as FastAPI
    participant Engine as WorkflowEngine
    participant DB as SQLite
    Caller->>API: Approval request with user_id
    API->>API: Validate request shape
    API->>Engine: approve_step(instance_id, step_id, user_id)
    Engine->>DB: BEGIN IMMEDIATE
    Engine->>DB: Load workflow and step
    Engine->>Engine: Check step membership, states, and assignee
    Engine->>DB: Update step, follow transition, insert events
    Engine->>Engine: Build detached state snapshot
    Engine->>DB: COMMIT
    Engine-->>API: WorkflowState
    API-->>Caller: Serialized response
```

The snapshot is built inside the transaction, but the method only returns successfully
after the context manager commits. An error anywhere in a mutation rolls back state and
audit events together. No API handler needs to coordinate a second commit.

Each call gets a fresh session. `BEGIN IMMEDIATE` reserves the SQLite writer before state
is read, so competing mutations are serialized. This is a local-prototype tradeoff, not
a high-throughput distributed locking strategy. SQLite's configured lock timeout is
10 seconds; exhausted contention is a database error, not a custom retry mechanism.

State and event reads use explicit `BEGIN` transactions. Multiple queries therefore see
a consistent database snapshot. Closing the session ends that transaction. Response
models are detached data, not live ORM objects with permission to persist changes.

## Definition Lifecycle

There are two classes named `WorkflowDefinition`, with different responsibilities:

- The Pydantic class in [definitions.py](../workflow_engine/definitions.py) holds the
  frozen Python specification, including its ordered steps.
- The ORM class in [models.py](../workflow_engine/models.py) stores only ID, name, and version.

When creating an instance, the engine finds or inserts the definition by `(name, version)`.
On first use it persists adjacent-step transitions. Every new instance receives its own
step rows and reviewer assignments. Later approvals follow the stored transition rows;
they do not ask an agent or inspect application data.

The API defaults to Technical Risk Assessment version 1. Engine construction and
`create_app(definition=...)` can supply another sequential approval specification with
one or more steps. `GET /workflow-definition` exposes the configured specification
read-only; the workbench generates assignments from it.

Increment the version when changing steps. Reusing a version with different Python steps
is rejected by comparing persisted transitions and instance steps. Existing instances keep their stored
steps and reference that version's transitions, not a private copy of the transition graph.

## Local Workbench

FastAPI serves the browser workbench at `/` and bundled assets under `/static`. The UI
uses the same-origin JSON endpoints and has no frontend build or external asset dependency.
`GET /workflows` reads all state snapshots newest first; it is unpaginated for this small
prototype. `GET /demo/users` returns the fixed mock-user directory from Python code.

Changing the selected user changes the submitted `user_id`. The browser disables actions
based on the creation event and current assignee, but these checks are only convenience:
the engine independently validates every action. Display roles confer no authority.
An unsuccessful post-mutation refresh disables further mutations until fresh data loads.

Send-back and forwarding use the same transactional engine boundary. Send-back validates
the stored path to an approved previous step, resets the target/downstream current states,
and activates the target. Forwarding changes only the current assignee after an injected
eligibility check. The API supplies the mock-user directory as its eligibility policy;
direct engine callers without a policy cannot forward. Original assignees and every prior
event remain intact, including across repeated rework cycles.

The UI does not seed assessments, log users in, add new tables, or call an agent. Browser
fixtures were used for UI checks while backend dependencies were unavailable; they are
not part of the application and do not prove engine behavior.

## Agent Boundary

`WorkflowAgent.run(context)` is abstract. `AgentContext` contains an instance ID and actor
ID, but is not an authenticated identity or an automatically enforced security policy.
The agent receives `WorkflowTools`, rather than a SQLAlchemy session.

| Tool | V0 behavior |
| --- | --- |
| `get_workflow_state` | Calls the injected reader and returns a deep copy of the snapshot |
| `get_application` | Returns copied external data if a reader was injected; otherwise raises `ToolUnavailable` |
| `get_available_roles` | Returns a copied list if a reader was injected; otherwise raises `ToolUnavailable` |
| `add_approval` | Raises `ToolUnavailable` |
| `request_information` | Raises `ToolUnavailable` |
| `escalate_workflow` | Raises `ToolUnavailable` |

There is no agent approval tool in V0. Future mutation tools must bind a verified actor
and call a deterministic engine operation. Do not enable placeholder methods by letting
them write directly to the database. This is a dependency boundary, not a sandbox that
can safely execute arbitrary Python from an untrusted agent.

## Startup And Shutdown

`create_app()` constructs the FastAPI application without opening the database. Its lifespan
handler opens SQLite on startup, creates missing tables, backfills legacy original
assignees through an additive migration, creates audit triggers, and installs
a `WorkflowEngine` on application state. Shutdown disposes the database engine.

Database URL selection is: an explicit `create_app(database_url)` argument, then
`WORKFLOW_DATABASE_URL`, then `sqlite:///./workflow.db`. The file path is relative to the
process working directory. The database helpers are SQLite-specific in V0.

## Security And Verification Limits

Permissions compare caller-supplied strings with the submitter or assigned reviewer.
They are deterministic but not authenticated. Reads are unrestricted, roles have no
permission meaning, and no tenant boundary exists. Keep this prototype local.

Audit triggers block row updates and deletes; a database administrator can remove them.
Foreign keys and enum checks do not replace engine-level workflow validation.

Source files passed syntax compilation during initial setup, but native dependency
installation prevented pytest and server startup verification. A temporary Docker runtime
with preinstalled libraries now runs the real app; browser approval and isolated API/SQLite
smoke checks passed. The full 87-test suite passed in an isolated container with offline
pytest 7.4.4 tooling; the declared pytest 8+ range remains unverified. See the
[current container setup](../README.md) for the networking workaround and restart commands.