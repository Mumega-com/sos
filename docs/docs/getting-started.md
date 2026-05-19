---
sidebar_position: 1
title: Getting Started
---

# Getting Started

## What is Mumega?

Mumega is an autonomous operations platform. You connect a project, and a team of specialized AI squads — SEO, Dev, Content, Outreach, Ops — starts working on it immediately. No manual task assignment. No project managers. The brain scores all open work across every project, claims the highest-value task, and executes it.

**One sentence:** Give Mumega a domain, it audits your site, creates a prioritized work queue, and runs autonomously until the work is done.

## How It Works in 30 Seconds

1. Register a project with a domain and squad list
2. The SEO squad audits the site and generates tasks automatically
3. The brain scores tasks by `impact × urgency / cost`
4. Squads claim and execute tasks — no human dispatch needed
5. Results are stored in memory, task marked done, next task begins

## Connect a Project

### 1. Register the project

```bash
curl -X POST https://api.mumega.com/projects \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "myproject",
    "domain": "myproject.com",
    "priority": "high",
    "squads": ["seo", "dev", "content"]
  }'
```

### 2. Trigger an audit

The audit creates tasks automatically — no manual setup needed.

```bash
curl -X POST https://api.mumega.com/projects/myproject/audit \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### 3. Watch it run

```bash
curl https://api.mumega.com/tasks?project=myproject \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Tasks move from `backlog` → `claimed` → `done` automatically. The brain runs continuously, dispatching the highest-scoring task to the matching squad.

## What Happens Next

- SEO squad fixes meta tags, generates schema markup, updates sitemaps
- Content squad drafts copy for gaps the audit identified
- Dev squad runs CI/CD pipelines when code tasks complete
- Human-judgment tasks surface in Discord for your team to claim

See [Onboard a Project](guides/onboard-project) for the full walkthrough.
