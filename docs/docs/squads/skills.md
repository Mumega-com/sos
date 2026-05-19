---
sidebar_position: 4
title: Skills
---

# Skills

## What Are Skills?

Skills are the executable capabilities that squads use to complete tasks. Each skill declares what it can do, what inputs it needs, and what it produces. The brain matches incoming tasks to the best available skill automatically — no routing rules to configure.

27 skills are available out of the box, covering SEO audits, content generation, code review, outreach drafting, infrastructure checks, and more.

## How Skill Matching Works

When the brain claims a task, it compares the task's labels and description against all registered skills. The highest-confidence match wins. If no match exceeds the confidence threshold, the task is held for human review.

## SKILL.md Format

Each skill is defined by a `SKILL.md` file with a YAML frontmatter block:

```markdown
---
name: seo_audit
version: 1.0.0
labels: [seo, audit]
trust_tier: T2
inputs:
  url:
    type: string
    required: true
outputs:
  report:
    type: object
  score:
    type: number
---

# SEO Audit

Run a full SEO audit on the given URL. Check title, description,
h1, canonical tags, and page speed. Return a structured report
with a 0-100 score and actionable recommendations.
```

Inputs are validated before execution. Invalid inputs return `400`. Invalid outputs halt execution and are logged for review.

## Trust Tiers

Skills run with different levels of system access depending on their trust tier:

| Tier | Level | Environment |
|------|-------|-------------|
| T1 | Unvetted | Sandboxed, no external access |
| T2 | Reviewed | Code-reviewed and tested |
| T3 | Certified | Audited, production-proven |
| T4 | Vendor | External partner, SLA-backed |

New custom skills start at T1. Promote after review and testing.

## Register a Custom Skill

```bash
curl -X POST https://api.mumega.com/skills \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my_skill",
    "definition": "<SKILL.md contents>",
    "trust_tier": "T1"
  }'
```

See [Create a Skill](../guides/create-skill) for a full walkthrough.
