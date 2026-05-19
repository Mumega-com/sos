---
sidebar_position: 5
title: Pipelines
---

# Pipelines

## What Are Pipelines?

Pipelines attach to squads and run automatically when code tasks complete. Build, test, deploy, smoke test — in sequence. If any step fails, the pipeline halts and the task is flagged for review.

## Pipeline Lifecycle

```
pending → building → testing → deploying → smoke → succeeded
                                                  ↘ failed
```

| State | What's Happening |
|-------|-----------------|
| `pending` | Pipeline attached, waiting for a trigger |
| `building` | Build command running |
| `testing` | Test suite running |
| `deploying` | Deploy command running |
| `smoke` | Health check running |
| `succeeded` | All steps passed |
| `failed` | A step exited non-zero |

Steps with `null` commands are skipped automatically.

## Attach a Pipeline

```bash
curl -X PUT https://api.mumega.com/squads/:squad_id/pipeline \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "build_cmd": "npm run build",
    "test_cmd": "npm test",
    "deploy_cmd": "npx wrangler deploy",
    "smoke_cmd": "curl -f https://myproject.com/health"
  }'
```

## Trigger a Run

```bash
curl -X POST https://api.mumega.com/squads/:squad_id/pipeline/run \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## Check Status

```bash
curl https://api.mumega.com/squads/:squad_id/pipeline/status \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## GitHub Actions Integration

Trigger a Mumega pipeline run from any GitHub push:

```yaml
# .github/workflows/notify-mumega.yml
on:
  push:
    branches: [main]

jobs:
  notify:
    runs-on: ubuntu-latest
    steps:
      - name: Trigger Mumega pipeline
        run: |
          curl -X POST https://api.mumega.com/squads/YOUR_SQUAD_ID/pipeline/run \
            -H "Authorization: Bearer ${{ secrets.MUMEGA_TOKEN }}"
```

Pipelines can also be triggered automatically by the brain when a dev task completes — no webhook setup required.
