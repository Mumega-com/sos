---
sidebar_position: 2
title: Mirror API
---

# Mirror API

Base URL: `https://api.mumega.com/memory`

Mirror is the persistent memory layer for all agents. Every task result, decision, and significant event is stored here as an engram — a semantically-indexed memory that can be retrieved by meaning, not just exact match.

Built on pgvector. All searches are semantic (embedding-based), not keyword-based.

## Store a Memory

```bash
curl -X POST https://api.mumega.com/memory/store \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "SEO audit complete. Score: 72/100. Main issues: missing meta descriptions on 14 pages, no schema markup on product pages.",
    "agent": "your-agent-id",
    "context_id": "myproject-seo-2026-04"
  }'
```

Response:

```json
{ "id": "eng_abc123", "stored": true }
```

## Search Memories

Semantic search — finds relevant memories even when the exact words don't match.

```bash
curl -X POST https://api.mumega.com/memory/search \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "what SEO issues were found",
    "agent": "your-agent-id",
    "limit": 5
  }'
```

Response:

```json
{
  "results": [
    {
      "id": "eng_abc123",
      "text": "SEO audit complete. Score: 72/100...",
      "score": 0.94,
      "created_at": "2026-04-05T10:00:00Z"
    }
  ]
}
```

`score` is cosine similarity (0–1). Results above 0.7 are typically high-relevance matches.

## List Recent Memories

```bash
curl "https://api.mumega.com/memory/list?agent=your-agent-id&limit=20" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## Health Check

```bash
curl https://api.mumega.com/memory/health
```
