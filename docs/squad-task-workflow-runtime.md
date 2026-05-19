# Squad Task Workflow Runtime

S028 splits durable execution state out of `squad_tasks`.

## Tables

- `squad_tasks`: intent/work item and terminal task state.
- `task_runs`: one execution attempt for a task. Carries `claim_token`,
  `idempotency_key`, and `correlation_id`.
- `task_steps`: progress checkpoints inside a run.
- `task_events`: durable state transitions. Duplicate events are suppressed by
  `(tenant_id, run_id, idempotency_key)` when an idempotency key is supplied.
- `task_artifacts`: output/proof/file pointers. Duplicate artifacts are
  suppressed by `(tenant_id, run_id, idempotency_key)` when supplied.
- `task_approvals`: human/agent gates for future approval workflows.

Every table has `tenant_id`; all Squad service methods filter by tenant before
returning rows.

## Lifecycle

1. Task is created in `squad_tasks`.
2. Agent claims task and receives the current `claim_token`.
3. Agent starts a run with `POST /tasks/{task_id}/runs`.
4. Agent writes steps/events/artifacts with `POST /runs/{run_id}/...`.
5. Approval gates are represented in `task_approvals` when needed.
6. Run completes/fails/cancels through events and status updates.
7. Task completes/fails through the existing task endpoints.

## Fencing

If a run start presents `claim_token`, Squad compares it with the current token
on `squad_tasks`. A stale token is rejected with the same fencing semantics as
task completion. This prevents a TTL-expired owner from writing a new execution
attempt after another owner has reclaimed the task.

## API

Read:

- `GET /tasks/{id}/runs`
- `GET /tasks/{id}/events`
- `GET /tasks/{id}/artifacts`

Write:

- `POST /tasks/{id}/runs`
- `POST /runs/{run_id}/steps`
- `POST /runs/{run_id}/events`
- `POST /runs/{run_id}/artifacts`

MCP remains thin: it may summarize this state, but Squad owns the workflow
tables and lifecycle rules.
