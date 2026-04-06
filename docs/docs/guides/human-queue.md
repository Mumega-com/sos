---
sidebar_position: 3
title: Human Queue
---

# Human Queue

AI handles 80% of execution. The human queue routes the remaining 20% — judgment calls, legal review, creative approval, relationship management — to your team via Discord.

## How It Works

1. A task is created or escalated with label `needs_human`
2. The platform posts it to your team's `#task-queue` Discord channel
3. A team member claims it with `!claim`
4. They complete the work and submit results with `!done`
5. The task closes and the brain continues

If an AI-executed task fails twice, it is automatically escalated to the human queue. No stuck tasks.

## Discord Commands

| Command | Description |
|---------|-------------|
| `!queue` | List all unclaimed human tasks |
| `!claim <task_id>` | Claim a task — locks it to you |
| `!done <task_id> <result>` | Submit result and close the task |
| `!mine` | List your currently claimed tasks |

## Example Session

```
!queue
> [T-042] Write testimonial email for new customer (high) — labels: content, needs_human
> [T-051] Review legal copy before launch (critical) — labels: legal, needs_human

!claim T-042
> Claimed T-042. It's yours.

!done T-042 "Draft uploaded to Notion: https://notion.so/..."
> Done. T-042 marked complete.
```

## Create a Human Task via API

```bash
curl -X POST https://api.mumega.com/tasks \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Approve homepage copy before launch",
    "project": "myproject",
    "labels": ["content", "needs_human"],
    "priority": "high",
    "description": "Review for tone and accuracy. Approve or suggest edits."
  }'
```

## Automatic Escalation

Any AI task that fails execution twice is automatically relabeled `needs_human` and posted to the queue. This ensures no task is permanently stuck.
