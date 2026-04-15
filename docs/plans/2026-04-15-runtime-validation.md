# SOS Runtime Validation — 2026-04-15

## Scope

Validated the current runtime diff for:

- `sos/contracts/squad.py`
- `sos/mcp/sos_mcp_sse.py`
- `sos/services/health/lifecycle.py`
- `sos/services/squad/service.py`

Reviewed change context with the code graph and refreshed the graph after validation.

## Checks Run

### Change-aware review

- `detect_changes` on the SOS repo
- `get_review_context` for the four runtime files above

Result:

- Risk score remains high (`0.80`)
- Wide blast radius across Squad, MCP auth, and lifecycle flows
- Existing graph still reports test gaps for the touched runtime paths

### Syntax / compile validation

Command:

```bash
uv run python -m py_compile \
  /home/mumega/SOS/sos/contracts/squad.py \
  /home/mumega/SOS/sos/mcp/sos_mcp_sse.py \
  /home/mumega/SOS/sos/services/health/lifecycle.py \
  /home/mumega/SOS/sos/services/squad/service.py
```

Result:

- Passed

### Focused lifecycle tests

First attempt:

```bash
uv run python -m pytest /home/mumega/SOS/tests/test_lifecycle_contract.py -q
```

Result:

- Failed because `redis` was not available in the transient environment
- Failure was environmental, not an assertion failure in the runtime diff

Second attempt:

```bash
uv run --with redis --with requests python -m pytest /home/mumega/SOS/tests/test_lifecycle_contract.py -q
```

Result:

- Passed (`4 passed`)

### Targeted MCP and Squad runtime tests

Command:

```bash
uv run --with requests python -m pytest \
  /home/mumega/SOS/tests/test_lifecycle_contract.py \
  /home/mumega/SOS/tests/test_mcp_cloudflare_auth.py \
  /home/mumega/SOS/tests/test_squad_runtime.py -q
```

Result:

- Passed (`9 passed`)

## Findings

### 1. Lifecycle validation is good once runtime deps are present

The changed lifecycle behavior around `parked`, stuck threshold, and payload field normalization passes the focused lifecycle contract tests when `redis` and `requests` are available.

### 2. Dependency drift was present and is now corrected locally

`pyproject.toml` did not previously declare several practical runtime dependencies used by the changed service path.

Fixed locally:

- `python-dotenv`
- `redis`
- `requests`
- `sse-starlette`

This removes the packaging mismatch that previously blocked `sos_mcp_sse.py` validation in a minimal environment.

### 3. One unrelated test path is currently broken

Attempting to collect `security/test_capability.py` failed because it imports:

- `sos.security.capability`

That module path does not exist in the current package layout.

This was not part of the runtime diff under review, but it is a real repo validation issue.

## Graph Refresh

Graph update command:

- incremental update via code-review-graph

Result:

- 16 files re-parsed
- 188 nodes and 1545 edges updated
- graph timestamp updated to `2026-04-15T15:09:14`

Current graph stats:

- Files: `335`
- Nodes: `3808`
- Edges: `21892`

## Validation Verdict

### Passed

- Changed runtime files compile
- Lifecycle contract tests pass with required runtime deps present
- Targeted MCP and Squad runtime tests pass
- Dependency declarations now match the validated runtime path
- Graph is current

### Not fully validated

- Repo has unrelated failing validation paths outside this diff

## Recommended Next Fixes

1. Fix the unrelated `security/test_capability.py` import path
2. Add more behavioral coverage around `handle_tool()` and `SquadService` flows with tenant scoping
3. Keep the graph refreshed when staging the publish branch so review context matches the pushed diff
