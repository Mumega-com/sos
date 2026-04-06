---
name: link-analyzer
description: Map internal link structure of a website. Finds orphan pages, under-linked pages, and link equity gaps. Use when internal linking needs improvement or after a site audit.
labels: [links, internal_linking]
keywords: [internal link, link map, orphan page, link equity]
entrypoint: sovereign.skills.seo:internal_link_analyzer
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
      description: Optional list of specific page URLs to crawl
  required: [url]
output_schema:
  type: object
  properties:
    pages_crawled: {type: integer}
    orphan_pages: {type: array}
    under_linked: {type: array}
    over_linked: {type: array}
---

# Link Analyzer

Builds an internal link map for a website and identifies linking issues.

## What it finds
- Orphan pages (zero inbound links)
- Under-linked pages (1-2 inbound links)
- Over-linked pages (10+ outbound links)
- Link equity distribution gaps

## When to use
- After site audit identifies linking issues
- When planning internal linking strategy
- Before major site restructuring
