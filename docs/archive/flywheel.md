# Flywheel

The flywheel is the feedback loop that makes SOS learn. Every action gets scored. Scores shape future behavior. The system gets better with use.

## The cycle

```
    ┌──────────┐
    │  ACTION   │ ← Agent does work
    └────┬─────┘
         │
         ▼
    ┌──────────┐
    │ FEEDBACK  │ ← Result gets scored (0-100)
    └────┬─────┘
         │
         ▼
    ┌──────────┐
    │  INGEST   │ ← Score + context stored in analytics
    └────┬─────┘
         │
         ▼
    ┌──────────┐
    │  DECIDE   │ ← Adaptation rules adjust weights
    └────┬─────┘
         │
         ▼
    ┌──────────┐
    │   ACT     │ ← Next action is smarter
    └────┬─────┘
         │
         └──────── loops back to ACTION ──────►
```

## How feedback scoring works

When a task completes, the feedback service scores it:

```python
# sos/services/feedback/loop.py

async def score_result(task_id: str, result: dict) -> int:
    """Score a completed task result from 0-100."""
    # Factors:
    # - Did the task succeed? (+40)
    # - Was it completed within time estimate? (+20)
    # - Did it require human intervention? (-10)
    # - Quality signal from downstream (analytics, user feedback) (+40)
    ...
```

Scores emit a `feedback.scored` event that other services can react to.

## Adaptation rules

The feedback service generates adaptation rules based on accumulated scores:

- **Agent routing**: If agent X consistently scores higher on code tasks, route more code tasks to X
- **Model selection**: If Gemma handles simple tasks well (score > 80), keep using it instead of upgrading to Opus
- **Task priority**: If a task type consistently fails, auto-escalate priority on next occurrence
- **Skill matching**: Match tasks to agents based on skill scores, not just availability

## Mirror compounds knowledge

Every scored result also flows to Mirror (memory):

1. Task context + result stored as an engram
2. Vector embedding generated for semantic search
3. Future tasks can recall similar past results
4. Agents learn from collective memory, not just their own

```python
# After scoring
await mirror.remember(
    text=f"Task {task.type}: {task.result} (score: {score})",
    metadata={"task_id": task.id, "agent": task.agent, "score": score},
)

# Before executing a similar task
similar = await mirror.recall(f"how to handle {task.type}")
# Returns past results with scores -- agent uses this context
```

## Analytics pipeline

The analytics service runs a three-phase pipeline:

### 1. Ingest (`analytics/ingest.py`)
Collects signals: task completions, feedback scores, agent activity, content metrics, revenue data.

### 2. Decide (`analytics/decide/`)
Evaluates signals against rules and thresholds. Decides what actions to take.

### 3. Act (`analytics/act.py`)
Executes decisions: reassign tasks, adjust model routing, trigger outreach, create new tasks.

## The weekly rhythm

While the flywheel runs continuously, it has a natural weekly pulse:

- **Monday**: Brain reviews past week's scores, creates priority tasks
- **Daily**: Feedback loop scores completions, updates adaptation rules
- **Friday**: Analytics runs full portfolio review, adjusts strategy for next week

## What makes it compound

Each cycle adds data. More data improves scoring. Better scoring improves routing. Better routing improves results. Better results produce more data.

The organism does not reset between tasks. It remembers.
