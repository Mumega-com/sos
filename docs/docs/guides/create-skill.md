---
sidebar_position: 2
title: Create a Skill
---

# Create a Custom Skill

Extend Mumega with capabilities specific to your use case. Skills are self-describing — define inputs, outputs, and instructions, and the brain starts matching tasks to your skill automatically.

## 1. Define the Skill

Create a `SKILL.md` with a YAML frontmatter block:

```markdown
---
name: competitor_analysis
version: 1.0.0
labels: [seo, research, competitor]
trust_tier: T1
inputs:
  domain:
    type: string
    required: true
  competitor:
    type: string
    required: true
outputs:
  gaps:
    type: array
  recommendations:
    type: array
---

# Competitor Analysis

Compare two domains and identify content and keyword gaps.

## Instructions

1. Fetch both domains' sitemaps
2. Extract top pages and keywords for each
3. Find gaps where the competitor ranks but the target domain does not
4. Return actionable recommendations ranked by opportunity
```

## 2. Add an Executor (Optional)

For custom logic, submit an executor alongside the definition:

```python
from __future__ import annotations
from dataclasses import dataclass

@dataclass
class SkillInput:
    domain: str
    competitor: str

@dataclass
class SkillOutput:
    gaps: list[str]
    recommendations: list[str]

async def execute(inputs: SkillInput) -> SkillOutput:
    # Your logic here
    return SkillOutput(gaps=[], recommendations=[])
```

## 3. Register the Skill

```bash
curl -X POST https://api.mumega.com/skills \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "competitor_analysis",
    "definition": "<SKILL.md contents>",
    "trust_tier": "T1"
  }'
```

## 4. Test It

```bash
# Verify registration
curl "https://api.mumega.com/skills?name=competitor_analysis" \
  -H "Authorization: Bearer YOUR_TOKEN"

# Test skill matching against a task
curl -X POST https://api.mumega.com/skills/match \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "task_title": "Analyze competitor SEO gaps for myproject",
    "labels": ["seo", "competitor"],
    "project": "myproject"
  }'

# Execute directly
curl -X POST https://api.mumega.com/skills/execute \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "skill": "competitor_analysis",
    "inputs": {
      "domain": "myproject.com",
      "competitor": "competitor.com"
    }
  }'
```

New skills start at `T1` (sandboxed). Request a trust tier upgrade after testing in production.
