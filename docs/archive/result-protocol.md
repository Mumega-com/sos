# Task Result Protocol

When completing a task, agents MUST output structured results in this format.
The output capture logger parses these patterns every 60 seconds and forwards
them to Squad Service (task completion) and Mirror (memory storage).

## Required Format

```
RESULT: task_id=<task_id> status=completed
SUMMARY: <one-line description of what was done>
VERIFY: <URL or command to verify the work>
```

## Optional Patterns

```
DONE: <message>          — General completion signal (no task_id needed)
ERROR: <message>         — Report an error or blocker
```

## Status Values

| Status | Meaning |
|--------|---------|
| `completed` | Task finished successfully |
| `failed` | Task attempted but failed |
| `blocked` | Task blocked by external dependency |
| `partial` | Some subtasks done, more needed |

## Example

```
RESULT: task_id=abc123 status=completed
SUMMARY: Rewrote /pricing page hero section. Added testimonials carousel.
VERIFY: Check https://viamar.ca/pricing — hero should show new copy
```

## How It Works

1. Agent outputs structured text to their tmux session
2. `output_capture.py` runs every 60s, captures pane diff
3. Regex parser extracts RESULT/SUMMARY/VERIFY/DONE/ERROR
4. Events forwarded to:
   - **Squad Service** (`POST /tasks/{id}/complete`) — closes the task
   - **Mirror** (`POST /store`) — stores as agent memory
   - **Redis** (`sos:stream:output_capture`) — real-time consumers
   - **Redis pub/sub** (`sos:events:task.completed`) — event bus

## For Agent Developers

Add this to your agent's CLAUDE.md or system prompt:

```
When you complete a task, output:
RESULT: task_id=<id> status=completed
SUMMARY: <what you did>
VERIFY: <how to verify>
```
