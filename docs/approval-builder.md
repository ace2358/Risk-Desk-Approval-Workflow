# Dynamic Approval Builder

[Project setup](../README.md) | [Architecture](architecture.md) | [Database](database-design.md)

## Scope And Design

The existing execution rules already supported arbitrary step counts. This feature adds a
user-edited ordered list, persisted independently of instances, and application use cases
for saving/retrieving it and creating executions. There is no maximum approval count and
no first/second/third-step routing logic. Existing domain approval, rejection, send-back,
forwarding, cancellation, permission and audit logic remains in use.

ApprovalWorkflowDefinition validates a nonempty tuple of ApprovalStepDefinition objects.
Each object contains stable string ID, nonblank unique name, explicit one-based order,
and nonblank individual assignee. IDs must be unique and the supplied collection must be
ordered exactly 1..N. The service checks assignees using the injected eligibility policy;
the API uses existing mock users, whose roles are labels rather than permission grants.

The repository stores the collection in nullable JSON `workflow_definitions.approval_steps`.
No fixed approval fields or new tables are needed. Stored definitions translate to the
existing sequential specification and adjacent transitions. Execution rows use separate
generated IDs and assignments; forwarding never edits definition defaults.

Saved name/version pairs are immutable. An identical save is idempotent and returns the
same ID. Changed IDs, names, order or assignees require an incremented version. Stable step
IDs can be reused in the new version. Existing instances continue referencing their
unchanged version and retain their current execution state and complete audit history.

## UI

The existing New assessment dialog now contains workflow name/version, a saved-workflow
selector, and configurable approval rows. Add Approval appends a blank row; up/down arrows
reorder without drag-and-drop; remove deletes a draft row and is disabled for the last row.
Inputs and assignee choices survive reordering. No page reload is needed.

Save definition does not require an application ID or create an instance. Create assessment
saves/reuses the definition, then creates a Pending workflow through a separate request.
If instance creation fails, the saved definition remains reusable. Selecting a saved version
loads its full ordered list. Editing it advances the draft version rather than mutating the
saved one. Cancel discards unsaved edits. The dialog scrolls for long lists on mobile.

## API Examples

`POST /workflow-definitions` returns HTTP 201 and a generated numeric definition ID:

```json
{
  "name": "Product risk review",
  "version": 1,
  "steps": [
    {"id": "engineer-review", "name": "Engineer", "order": 1, "assigned_to": "engineer"},
    {"id": "manager-review", "name": "Manager", "order": 2, "assigned_to": "manager"},
    {"id": "quality-review", "name": "Quality", "order": 3, "assigned_to": "engineer"},
    {"id": "safety-review", "name": "Safety", "order": 4, "assigned_to": "manager"},
    {"id": "director-review", "name": "Director", "order": 5, "assigned_to": "final"}
  ]
}
```

These names are examples only. Use any valid nonempty sequence. `GET /workflow-definitions`
lists saved builder definitions newest first. `GET /workflow-definitions/{definition_id}`
returns the ID, name, version, and every approval with its stable ID and explicit order.

Create an instance with `POST /workflow-definitions/{definition_id}/workflows`:

```json
{"entity_type": "application", "entity_id": "product-123", "user_id": "submitter"}
```

The response is the existing WorkflowState with Pending execution steps. Call
`POST /workflows/{instance_id}/start` with `{"user_id":"submitter"}` to activate the first.
Each assigned reviewer uses the existing approval endpoint with the execution step ID.
Completion occurs only after final approval. Rejection, cancellation, send-back and
forwarding use the unchanged endpoint contracts and permissions.

Invalid definitions, unknown assignees and conflicting versions return 422. Missing saved
definition IDs return 404. Unauthorized instance actions return 403. Inactive/terminal
actions return 409. No authentication or definition-level ownership model was added.
Definitions are configuration, not execution audit events; instance actions remain audited.

Legacy Python configuration and `/workflow-definition`/`POST /workflows` remain available.
Rows created by that path have no builder collection and are omitted from the builder list;
the configured default supplies initial draft rows but does not impose a limit.

## Migration And Verification

Startup adds nullable `approval_steps JSON` under the existing `BEGIN IMMEDIATE` migration
transaction if absent. Existing definition IDs, rows, transitions, assignments and audit
events are untouched; legacy JSON values remain NULL. Initialization is idempotent. No
database is deleted or recreated and existing audit triggers stay active.

Before the live migration, SQLite's backup API created
`/data/workflow-before-builder-20261009-034318.db` in the persistent volume. All original
column values were compared against it after startup: 7 instances and 56 events were
preserved. The verification then deliberately added saved definition #2 and completed
assessment #8 (`builder-ten-live`); those examples were retained, not deleted.

- Baseline: 104 tests passed. Final suite: **129 passed**, one existing upstream
  FastAPI TestClient/HTTPX deprecation warning.
- [Builder tests](../tests/test_builder.py) cover counts 1/3/5/10, all ten approvals,
  add/remove/reorder drafts, round trips/restart persistence, invalid configuration,
  immutable versions, a one-step version guard, migration, repeated rework, forwarding,
  rejection, and unauthorized actions without audit changes.
- Real browser: added to ten rows, removed/replaced a row, moved it to the top, saved and
  compared exact IDs/order, created an instance, approved through step nine, sent it back
  to step one, reapproved, forwarded final approval, and completed with **44 audit events**.
- Saved ten-row definition reload and mobile scrolling passed with no horizontal overflow.
  Editing preserved IDs and advanced the draft version. No JavaScript errors were seen.

Tests ran in the isolated Python 3.12 runtime with offline pytest 7.4.4 tooling. Native
`.venv` still lacks pytest because dependency installation is blocked; declared pytest 8+
compatibility remains unverified. No project dependency downgrade was made.

## Remaining Limits

Mock identities are not authentication. Assignees are individual users, not dynamically
resolved roles. Saved definitions are immutable versions, not editable active graphs.
Lists are unpaginated and the engine remains SQLite-specific and sequential. Unlimited
means no imposed step-count cap; practical memory/request/database limits still apply.
Parallel approvals, visual drag-and-drop, dynamic insertion into running workflows and
new agent tools are outside this iteration.