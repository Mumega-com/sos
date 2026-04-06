---
sidebar_position: 1
title: Squad Service API
---

# Squad Service API

Base URL: `https://api.mumega.com`

All requests require `Authorization: Bearer YOUR_TOKEN`.

## Squads

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/squads` | Create a squad |
| `GET` | `/squads` | List squads |
| `GET` | `/squads/:id` | Get squad |
| `PUT` | `/squads/:id` | Update squad |
| `DELETE` | `/squads/:id` | Delete squad |
| `POST` | `/squads/:id/members` | Add member |
| `PUT` | `/squads/:id/pipeline` | Set pipeline |
| `POST` | `/squads/:id/pipeline/run` | Trigger pipeline run |
| `GET` | `/squads/:id/pipeline/status` | Pipeline status |

### POST /squads

```bash
curl -X POST https://api.mumega.com/squads \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "content",
    "tier": "nomad",
    "skills": ["blog_post", "social_copy"],
    "labels": ["content", "copy"]
  }'
```

## Tasks

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/tasks` | Create task |
| `GET` | `/tasks` | List tasks (`?project=`, `?status=`, `?labels=`) |
| `GET` | `/tasks/:id` | Get task |
| `PUT` | `/tasks/:id` | Update task |
| `POST` | `/tasks/:id/claim` | Claim task |
| `POST` | `/tasks/:id/complete` | Mark done |
| `POST` | `/tasks/:id/fail` | Mark failed |

### POST /tasks

```bash
curl -X POST https://api.mumega.com/tasks \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Fix homepage meta description",
    "project": "myproject",
    "labels": ["seo"],
    "priority": "high",
    "description": "Current meta is 42 chars. Target: 155 chars with primary keyword.",
    "depends_on": []
  }'
```

### POST /tasks/:id/claim

```bash
curl -X POST https://api.mumega.com/tasks/task_xyz/claim \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"agent_id": "your-agent-id"}'
```

Returns `409 Conflict` if the task is already claimed.

## Skills

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/skills` | Register skill |
| `GET` | `/skills` | List skills |
| `GET` | `/skills/:id` | Get skill |
| `POST` | `/skills/match` | Match skill to task |
| `POST` | `/skills/execute` | Execute skill directly |

### POST /skills/match

```bash
curl -X POST https://api.mumega.com/skills/match \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_title": "Audit site SEO health",
    "labels": ["seo", "audit"],
    "project": "myproject"
  }'
```

Returns a ranked list of matching skills with confidence scores.

### POST /skills/execute

```bash
curl -X POST https://api.mumega.com/skills/execute \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill": "seo_audit",
    "inputs": { "url": "https://myproject.com" },
    "task_id": "task_xyz"
  }'
```

## Squad State

| Method | Path | Description |
|--------|------|-------------|
| `PUT` | `/state/:squad_id` | Set a state key |
| `GET` | `/state/:squad_id` | Get squad state |
