---
sidebar_position: 3
title: Brain
---

# Brain: The Portfolio Cortex

The brain is the autonomous prioritization and dispatch engine. It continuously scores all open work across every registered project and dispatches the highest-value task to the best available agent — without human orchestration.

## How It Works

The brain is event-driven, not cron-based. It wakes up when something meaningful happens: a new task arrives, a task completes, or an agent comes online. It re-scores the full portfolio and decides what to do next.

```
Event received (task.created / task.done / agent.woke)
      ↓
Cortex scores all open tasks across all projects
      ↓
Brain selects: which task, which agent, which skill
      ↓
Squad Service claims the task (atomic lock)
      ↓
Skill executes → result stored → task closed
      ↓
Bus event published → brain re-evaluates
```

## Scoring Formula

```
score = (impact × urgency × unblock_value) / cost

urgency   = { critical: 4.0, high: 2.0, medium: 1.0, low: 0.5 }
impact    ∈ [1, 10]   — estimated value delivered
unblock   = tasks waiting on this one to complete
cost      ∈ [0.1, 10] — execution cost, time, and risk
```

A low-priority task that unblocks 10 downstream tasks scores higher than a medium-priority standalone task. The formula surfaces real bottlenecks automatically.

Ties are broken by creation time — oldest first.

## Multi-Model Dispatch

The brain uses the cheapest model capable of the decision:

- Routine scoring and label matching → Gemma (local, near-zero cost)
- Complex prioritization with context → Gemini or Claude
- High-stakes decisions with full context → GPT-4 or Claude Opus

This keeps operating costs low while preserving quality for work that requires it.

## Event Triggers

| Event | What the Brain Does |
|-------|---------------------|
| `task.created` | Re-score the portfolio; new task may be highest priority |
| `task.done` | Unblock dependent tasks; re-score what's now available |
| `agent.woke` | Agent available; check if any unclaimed task matches it |

The brain never polls. Every decision is triggered by a real event.
