---
name: schema-checker
description: Validate JSON-LD structured data markup on a website. Checks for required schema types and fields, suggests missing schemas for the site's vertical. Use when schema markup needs auditing or adding.
labels: [schema, schema_markup]
keywords: [schema, json-ld, structured data, rich results]
entrypoint: sovereign.skills.seo:schema_checker
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
    schemas_found: {type: integer}
    types_found: {type: array}
    missing_recommended_types: {type: array}
    suggestions: {type: array}
    issues: {type: array}
---

# Schema Checker

Validates JSON-LD structured data and suggests improvements.

## What it checks
- Existing JSON-LD schemas on the page
- Required fields per schema type
- Missing recommended schema types for the vertical
- Dental/healthcare specific: Dentist, MedicalBusiness, LocalBusiness
- General: Organization, WebSite, BreadcrumbList, FAQPage, AggregateRating

## When to use
- After site audit finds zero or missing schemas
- When adding rich results support
- Before launching new page types
