---
sidebar_position: 1
title: Overview
---

# Squads

## What Are Squads?

Squads are specialized AI teams you deploy against any project. Each squad owns a domain of work — SEO, Dev, Content, Outreach, or Ops — and processes tasks labeled for that domain.

Think of it as hiring a full-stack team in one API call. The squad handles routing, execution, and result delivery. You define the work; the squad does it.

## The Five Squad Types

| Squad | What It Does |
|-------|-------------|
| `seo` | Site audits, meta optimization, schema markup, sitemap management |
| `dev` | Code tasks, pull requests, CI/CD pipeline runs, bug fixes |
| `content` | Blog posts, landing page copy, social content, email drafts |
| `outreach` | Cold outreach, partner contact, LinkedIn campaigns |
| `ops` | Infrastructure monitoring, cron management, incident response |

## How Squads Work

Tasks are labeled when created. The brain matches labels to squads automatically.

```
Task created with label: ["seo"]
      ↓
Brain scores task (impact × urgency / cost)
      ↓
SEO squad claims highest-scoring task
      ↓
Skill matched → executed
      ↓
Result stored, task marked done
```

No manual dispatch. The brain decides what gets worked on next across all projects simultaneously.

## Squads Serve Any Project

A squad is not tied to one project. The `seo` squad processes SEO tasks from every project you've registered — each scored and prioritized against the others. Your most urgent work always gets attention first.

## Squad Tiers

| Tier | Behavior | Human Review |
|------|----------|--------------|
| `nomad` | Default. Fast, flexible, no reserved capacity. | Required |
| `fortress` | Reserved capacity with SLA guarantees. | Optional |
| `construct` | Fully autonomous. Executes without review. | None |

Start with `nomad`. Promote to `construct` once you've verified a squad's output quality for your use case.
