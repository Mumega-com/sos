---
name: migration-check
description: Verify database schema matches code queries before deploying. Catches missing columns, broken joins, schema drift. Use before every deploy that touches DB queries.
labels: [deploy, migration, db, verify]
keywords: [migration, schema, column, database, supabase, deploy check]
entrypoint: ""
fuel_grade: diesel
trust_tier: 4
version: "1.0.0"
---

# Migration Check

Before deploying code that changes database queries, verify the schema exists.

## What to check
1. Every `.select('...')` in Supabase queries — do those columns exist?
2. Every `.from('table')` — does the table exist?
3. Every join (`.select('..., relation:other_table(...)')`) — does the FK relationship exist?
4. Any new columns referenced in code — is there a migration for them?

## How to check (Supabase)
```bash
# List columns in a table
curl -s "${SUPABASE_URL}/rest/v1/dnu_cities?select=*&limit=0" \
  -H "apikey: ${SUPABASE_KEY}" -I | grep -i "content-profile"

# Or query information_schema
# SELECT column_name FROM information_schema.columns WHERE table_name = 'dnu_cities';
```

## Rule
**If code references a column that doesn't exist in the database, the deploy MUST include a migration that adds it.** No exceptions.

Silent `return []` on Supabase errors hides these failures. The cities page was empty for this reason on 2026-04-05.

## Prevention
Before merging any PR that modifies a `.select()` or `.from()` call:
1. Check if new columns/tables exist in production DB
2. If not, create and run migration first
3. Run post-deploy check after deploy
