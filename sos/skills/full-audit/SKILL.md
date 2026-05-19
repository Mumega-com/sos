---
name: full-audit
description: Run all 4 SEO skills (site audit, meta optimizer, link analyzer, schema checker) and combine results into a comprehensive report. Use for complete SEO assessment of a project.
labels: [full-audit]
keywords: [full audit, complete audit, seo audit]
entrypoint: sovereign.skills.seo:run_full_audit
fuel_grade: diesel
trust_tier: 4
version: "1.0.0"
input_schema:
  type: object
  properties:
    url:
      type: string
      description: Target website URL
  required: [url]
output_schema:
  type: object
  properties:
    site_audit: {type: object}
    meta_optimizer: {type: object}
    internal_links: {type: object}
    schema_checker: {type: object}
    total_issues: {type: integer}
---

# Full SEO Audit

Runs all 4 SEO skills sequentially and combines into one report.

## Includes
1. Site Audit — technical SEO check
2. Meta Optimizer — title/description analysis
3. Link Analyzer — internal link mapping
4. Schema Checker — JSON-LD validation

## When to use
- First assessment of a new project
- Quarterly comprehensive review
- Before major launches
