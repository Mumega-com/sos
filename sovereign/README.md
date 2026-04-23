# Sovereign — Brain Layer

The thinking layer of the Mumega ecosystem. Perceives system state, scores work across all projects, and dispatches to agents.

## Components

| File | What | Runs as |
|------|------|---------|
| `cortex.py` | Portfolio-wide scoring — ranks all tasks by impact, urgency, unblock value, cost | CLI / imported by cortex_events |
| `cortex_events.py` | Event-driven brain wakeup — listens to Redis, triggers brain cycles on task events | systemd: cortex-events.service |
| `brain.py` | Decision engine — uses Gemma 4 31B (free) to pick highest-value action | imported by cortex_events |
| `loop.py` | Task executor — claims squad tasks, dispatches to agents via Redis | systemd: sovereign-loop.service |
| `registry.py` | Compute thread + squad definitions (legacy — migrating to Squad Service) | imported |
| `hive_evolution.py` | Self-improving task recipes with fuel grade escalation | imported |
| `skills/seo.py` | 5 executable SEO skills (site_audit, meta_optimizer, link_analyzer, schema_checker, full_audit) | CLI / imported |

## How It Works

```
Redis event (task.completed, task.failed, etc.)
  → cortex_events.py wakes up (60s debounce)
    → cortex.py takes portfolio snapshot
      → brain.py picks highest-value action (Gemma 4, free)
        → loop.py claims task in Squad Service (:8060)
          → dispatches to agent via Redis bus
```

## Scoring Formula

```
score = priority_weight * 10
      + unblock_count * 5      # tasks this one blocks
      + staleness_days * 2     # prevents starvation
      + revenue_bonus           # +20 for revenue projects
      + blocked_penalty         # -50 if status=blocked
```

## Quick Start

```bash
# See top priorities across all projects
python3 cortex.py next

# Full portfolio snapshot
python3 cortex.py snapshot

# Brain context (what the LLM sees)
python3 cortex.py context

# Run SEO audit
python3 skills/seo.py audit https://example.com
python3 skills/seo.py full https://example.com
```

## Services

```bash
# Check status
systemctl --user status cortex-events sovereign-loop

# Restart after code changes
systemctl --user restart cortex-events sovereign-loop
```

## Config

Brain routing is configured in `brain.py`:
- `LABEL_SQUAD_MAP` — maps task labels to squads (seo → seo squad, code → dev squad)
- `PROJECT_LEADS` — maps projects to lead agents (dentalnearyou → dandan)
- `resolve_squad(labels, project)` — determines which squad handles a task

## Organism YAMLs

Project configs at `~/.mumega/organisms/`:
```yaml
name: dentalnearyou
project_lead: dandan
budget_cents_monthly: 10000
workspace: /mnt/HC_Volume_104325311/dentalnearyou/web
squads:
  seo: true
  dev: true
```
