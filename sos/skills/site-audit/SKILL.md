---
name: site-audit
description: Run a technical SEO audit on a website. Checks title tags, meta descriptions, OG tags, H1 hierarchy, canonical URLs, robots.txt, sitemap.xml, response time, and JSON-LD schema. Use when auditing a site for SEO issues or when starting work on a new project.
labels: [audit, technical-seo]
keywords: [audit, crawl, check site, seo check]
entrypoint: sovereign.skills.seo:site_audit
fuel_grade: diesel
trust_tier: 4
version: "1.0.0"
input_schema:
  type: object
  properties:
    url:
      type: string
      description: Target website URL (e.g. https://dentalnearyou.ca)
  required: [url]
output_schema:
  type: object
  properties:
    url: {type: string}
    status: {type: integer}
    response_time_s: {type: number}
    meta: {type: object}
    headings: {type: object}
    robots: {type: object}
    sitemap: {type: object}
    schema: {type: object}
    issues: {type: array, items: {type: string}}
    issue_count: {type: integer}
  required: [url, status, issues, issue_count]
---

# Site Audit

Technical SEO audit for any website. Fetches the live page and checks all critical SEO elements.

## What it checks
- Title tag presence and length (50-60 chars optimal)
- Meta description presence and length (150-160 chars optimal)
- Open Graph tags (og:title, og:description, og:image, og:url)
- H1 tag count and hierarchy
- Canonical URL
- Robots.txt presence and sitemap references
- Sitemap.xml presence and URL count
- JSON-LD structured data
- Response time

## When to use
- Starting SEO work on a new project
- Regular monthly audits
- After major site changes
- When diagnosing indexing issues

## Example
```bash
python3 sovereign/skills/seo.py audit https://dentalnearyou.ca
```
