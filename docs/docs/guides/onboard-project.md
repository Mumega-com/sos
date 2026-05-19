---
sidebar_position: 1
title: Onboard a Project
---

# Onboard a Project in 5 Minutes

Give Mumega a domain. It audits, generates tasks, assigns squads, and runs autonomously.

## 1. Register the Project

```bash
curl -X POST https://api.mumega.com/projects \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "myproject",
    "domain": "myproject.com",
    "priority": "high",
    "squads": ["seo", "dev", "content"],
    "tags": ["saas", "b2b"]
  }'
```

The project is live immediately. The brain picks it up on its next cycle.

## 2. Run the Initial Audit

The SEO audit crawls the domain, scores technical health, and generates a task for every issue found — automatically labeled and prioritized.

```bash
curl -X POST https://api.mumega.com/projects/myproject/audit \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Typical audit output: 8–20 tasks covering missing meta tags, broken links, schema markup, page speed, and content gaps.

## 3. Add Any Manual Tasks

For work the audit doesn't cover, create tasks directly:

```bash
curl -X POST https://api.mumega.com/tasks \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Write homepage hero copy",
    "project": "myproject",
    "labels": ["content"],
    "priority": "high"
  }'
```

## 4. Attach a Deployment Pipeline (Optional)

If the project has a dev squad and deployable code:

```bash
curl -X PUT https://api.mumega.com/squads/YOUR_DEV_SQUAD_ID/pipeline \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "build_cmd": "npm run build",
    "test_cmd": "npm test",
    "deploy_cmd": "npx wrangler deploy",
    "smoke_cmd": "curl -f https://myproject.com/health"
  }'
```

## 5. Verify Autonomous Operation

```bash
curl "https://api.mumega.com/tasks?project=myproject" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Tasks move from `backlog` → `claimed` → `done` without any intervention. The brain continuously scores and dispatches. Human-judgment tasks surface in Discord for your team to claim.

That's it. The system runs until the backlog is clear, then waits for new work.
