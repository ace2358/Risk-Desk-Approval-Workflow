# Architectural Refactor Record

## Assessment And Plan

Before refactoring, the routes were already thin, the frontend used stable JSON contracts,
and agents were abstract interfaces with copied read tools. The main coupling was in
WorkflowEngine: rules, ORM queries, SQLite transactions, HTTP creation validation, and
response assembly lived together. Enums lived with ORM mappings, and API startup wired
concrete persistence alongside HTTP routes.

The executed plan was domain extraction with direct tests, application use cases and ports,
an ORM mapping adapter, legacy compatibility facades, presentation/composition separation,
agent import adaptation, and documentation. Validation followed each substantive step.
The primary compatibility risks were audit order, rollback, version reuse, schema aliases,
static paths, environment wiring, and public constructor/import behavior.

## Final Structure

```text
workflow_engine/
  domain/
    definitions.py       # reusable WorkflowDefinition and StepDefinition
    types.py             # statuses, errors, UTC clock
    workflow.py          # execution, assignment, transitions, audit intents and rules
  application/
    contracts.py         # validated command and detached snapshots
    ports.py             # repository and unit-of-work protocols
    service.py           # WorkflowService use cases
  infrastructure/
    models.py            # unchanged ORM schema
    database.py          # SQLite/session setup, existing additive migration, triggers
    repository.py        # ORM/domain mapping and SQLAlchemy unit of work
  presentation/
    api.py               # FastAPI routes, errors and static serving
    schemas.py           # HTTP requests and shared response models
    demo.py              # fixed mock identities
  bootstrap.py           # configuration, lifespan and concrete adapter wiring
  agents.py              # abstract agent and explicit application reader tools
  static/                # unchanged bundled frontend
  api.py                 # stable ASGI entry point and factory re-export
  engine.py              # legacy session-based WorkflowEngine constructor
  database.py            # compatibility re-exports
  models.py              # compatibility re-exports
  definitions.py         # compatibility re-exports
  schemas.py             # compatibility re-exports
  demo.py                # compatibility re-export
tests/
examples/rework.py
docs/
```

Each layer directory has an `__init__.py`. No new dependencies or deployment services
were introduced. Existing package discovery and static package data remain valid.

## Boundaries And Model Responsibilities

- Domain imports neither application, infrastructure, presentation nor agents. It has no
  SQLAlchemy, FastAPI, HTTP request or provider dependency. Existing Pydantic definition
  validation is retained for compatibility; execution rules use ordinary dataclasses.
- Application invokes the domain and coordinates persistence through its own Protocol
  interfaces. Its snapshots are shared data contracts, not ORM entities or HTTP objects.
- Infrastructure implements those interfaces and is the only layer with database sessions,
  queries, mapping, commit and rollback. Definition version rules are delegated to domain.
- Presentation validates request shapes, maps errors to HTTP, and invokes use cases.
  Bootstrap alone wires concrete persistence into the service for application startup.
- Agents retain their existing controlled readers, deep-copy guarantees, abstract run
  interface and unavailable mutation tools. They import application contracts, not sessions
  or HTTP schemas. No LLM integration or new mutation capability was enabled.

Reusable step configuration is separate from instance StepExecution state. ApprovalAssignment
separates current and first assignees without introducing a new table. Domain events are
frozen envelopes; persisted history remains protected by existing update/delete triggers.
Rework still resets current execution rows while preserving previous review events, rather
than creating a separate attempt table or introducing event sourcing.

## Compatibility And Changes

Moved implementation ownership from root definitions/models/database/API/schema/demo
modules into their layers. The original files remain as facades because tests and scripts
depend on them. The old engine implementation was replaced by a small WorkflowService
subclass that accepts the same sessions, definition and recipient-policy arguments.
No public module was removed; no duplicated old rule implementation remains.

Created all layer modules shown above and bootstrap. Modified root facades and agents,
updated the runnable example, README, architecture/database/flow guides, and project
instructions. Added test_domain, test_application, test_application_memory, and
test_architecture tests. Existing behavior test files and frontend assets were not changed
by this refactor. Environment variables, Docker configuration, API contracts and entry point
are preserved.

Approval validates its transition before in-memory mutation. Repository saves stage state
and events together, acknowledge pending events only after successful commit, and avoid
duplicate event inserts on repeated saves in one transaction. These changes do not add a
workflow feature or alter success/error HTTP contracts.

## Database And Verification

No new database migration is required. Table names, column types, enum strings, constraints,
indexes, audit triggers and the existing idempotent original-assignee backfill are unchanged.
Read-only data and schema SHA-256 comparisons matched before/after live startup: 7 instances,
21 steps, 56 events. Nothing was deleted or recreated.

- Before refactor: **87 passed**.
- After refactor: **104 passed**, one upstream FastAPI TestClient/HTTPX deprecation warning.
- New checks cover domain rules, in-memory application/agent isolation, inward imports,
  framework-blocked imports, legacy class identity, startup/environment/static integration,
  rollback after flush, commit failure, and repeated-save audit behavior.
- Five-step rework/John-to-Sarah example completed with 24 events using explicit service
  wiring and a temporary database.
- Real ASGI startup and the unchanged workbench were verified against the preserved volume.

Tests ran in a disposable Python 3.12 container using preinstalled runtime dependencies and
isolated offline pytest 7.4.4 tooling. The native command still fails with `No module named
pytest`; declared pytest 8+ compatibility remains unverified. No test dependency downgrade
was made in pyproject.toml. Three intermediate API failures from duplicate EventState
classes and one annotation import collision were fixed and rerun successfully.

## Limits And Next Steps

This remains SQLite-specific, sequential, unauthenticated and unpaginated. Compatibility
facades are intentional and should remain until external callers migrate. New rules should
start with domain tests and new use cases, extending ports only when persistence actually
requires it. Restore native dependency installation and rerun the declared pytest 8+ suite
before broader rollout. No dynamic builder, parallel approvals, AI integration, queue or
authentication feature was added.