---
name: meta-optimizer
description: Analyze and optimize title tags and meta descriptions across a website. Crawls pages via sitemap, checks length compliance, finds duplicates. Use when meta tags need fixing or after a site audit finds meta issues.
labels: [meta, meta_optimization]
keywords: [meta tag, title tag, description, meta optimization]
entrypoint: sovereign.skills.seo:meta_optimizer
fuel_grade: diesel
trust_tier: 4
version: "1.0.0"
input_schema:
  type: object
  properties:
    url:
      type: string
      description: Target website URL
    pages:
      type: array
      items: {type: string}
      description: Optional list of specific page URLs to check
  required: [url]
output_schema:
  type: object
  properties:
    pages_checked: {type: integer}
    issues: {type: array}
    duplicates: {type: object}
---

# Meta Optimizer

Crawls a site's pages and analyzes title tags and meta descriptions for SEO compliance.

## What it checks
- Title length (optimal: 50-60 characters)
- Meta description length (optimal: 150-160 characters)
- Duplicate titles across pages
- Duplicate descriptions across pages
- Missing titles or descriptions

## When to use
- After site_audit finds meta tag issues
- Before deploying content changes
- Monthly SEO maintenance
