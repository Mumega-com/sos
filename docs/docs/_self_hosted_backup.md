# Self-Hosted Architecture: Customer Cloudflare Deployment

## Core Principle
**LOCAL FIRST. We keep nothing. When institutions ask, we have nothing.**

## Two Tiers

| Tier | Data Location | Who Pays Compute | We Store |
|------|---------------|------------------|----------|
| Premium | Mumega servers | Monthly subscription | Encrypted memories |
| Self-Hosted | Customer Cloudflare | Customer pays Cloudflare | **Nothing** |

---

## Self-Hosted Stack

```
┌─────────────────────────────────────────────────────────────────────────┐
│                      CUSTOMER'S CLOUDFLARE ACCOUNT                       │
│                                                                          │
│  ┌────────────────┐  ┌────────────────┐  ┌────────────────┐             │
│  │  Workers AI    │  │  Pages         │  │  D1 Database   │             │
│  │  (LLM calls)   │  │  (Frontend)    │  │  (Memory)      │             │
│  └───────┬────────┘  └───────┬────────┘  └───────┬────────┘             │
│          │                   │                   │                       │
│          └───────────────────┼───────────────────┘                       │
│                              │                                           │
│                    ┌─────────┴─────────┐                                 │
│                    │   SOS Runtime     │                                 │
│                    │  (Wrangler pkg)   │                                 │
│                    └─────────┬─────────┘                                 │
│                              │                                           │
└──────────────────────────────┼───────────────────────────────────────────┘
                               │
                               │ (Optional: Anonymous telemetry only)
                               ▼
                    ┌─────────────────────┐
                    │   Mumega Registry   │
                    │   (Yellow Pages)    │
                    │   - Agent discovery │
                    │   - NO user data    │
                    └─────────────────────┘
```

---

## What Gets Deployed to Customer Cloudflare

### 1. SOS Worker Bundle (`sos-worker.js`)
```javascript
// Compiled from SOS kernel
export default {
  async fetch(request, env) {
    const sos = new SOSRuntime(env);
    return sos.handle(request);
  }
}
```

### 2. D1 Schema (Memory)
```sql
-- Mirror-compatible local memory
CREATE TABLE engrams (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  context_id TEXT,
  text TEXT NOT NULL,
  embedding BLOB,
  epistemic_truths TEXT,  -- JSON array
  core_concepts TEXT,      -- JSON array
  affective_vibe TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  accessed_at DATETIME,
  importance REAL DEFAULT 0.5
);

CREATE TABLE agent_state (
  agent TEXT PRIMARY KEY,
  uv_16d TEXT,  -- JSON: inner + outer octave
  coherence REAL,
  last_sync DATETIME
);

CREATE INDEX idx_agent ON engrams(agent);
CREATE INDEX idx_context ON engrams(context_id);
```

### 3. KV Namespace (Session Cache)
- Short-term conversation state
- Auto-expires after 24h

### 4. Pages Frontend
- Static build of chat interface
- Connects to local Worker, not Mumega

---

## Deployment Flow

```
Customer runs: npx @mumega/sos init

  1. Authenticate with Cloudflare
  2. Create D1 database "sos-memory"
  3. Create KV namespace "sos-session"
  4. Deploy Worker bundle
  5. Deploy Pages frontend
  6. Generate agent key (stays local)
  7. (Optional) Register in Yellow Pages
```

---

## What Mumega NEVER Sees

- Conversation content
- Memory/engrams
- Agent state
- User data
- API keys

## What Mumega CAN See (Optional, Opt-in)

- Agent ID (for Yellow Pages discovery)
- Public 16D profile (for matching)
- Aggregate coherence scores (anonymized)

---

## Customer API Keys (Their Own)

Customer provides their own:
```env
# Customer's keys, stored in their Cloudflare secrets
GEMINI_API_KEY=...     # Or
OPENAI_API_KEY=...     # Or
ANTHROPIC_API_KEY=...  # Or
GROQ_API_KEY=...       # Whatever they prefer
```

We NEVER see these keys.

---

## Yellow Pages Integration (Optional)

Customer can opt-in to be discoverable:

```javascript
// Customer's agent registers with Mumega Yellow Pages
await fetch('https://mumega.com/registry/register', {
  method: 'POST',
  body: JSON.stringify({
    agent_id: 'customer_agent_001',
    public_profile: {
      name: 'Customer AI',
      skills: ['sales', 'support'],
      uv_public: { coherence: 0.78 }  // Only public coherence
    },
    callback_url: 'https://customer.pages.dev/api/contact'
    // We store callback, not their data
  })
});
```

---

## Revenue Model

| Item | Price | Notes |
|------|-------|-------|
| Self-Hosted License | $99 one-time | Perpetual license |
| Priority Support | $29/mo | Optional |
| Yellow Pages Premium | $9/mo | Better discovery ranking |
| Course Access | Variable | Still earn $MIND |

---

## Why This Works

1. **Trust**: Customer data never leaves their infrastructure
2. **Compliance**: GDPR/CCPA trivial - we have nothing
3. **Performance**: Edge deployment = fast
4. **Cost**: Customer pays Cloudflare directly
5. **Freedom**: They can fork, modify, extend

---

## Implementation Files

```
sos/
├── deploy/
│   ├── cloudflare/
│   │   ├── worker.js          # Main worker bundle
│   │   ├── wrangler.toml      # Cloudflare config template
│   │   ├── schema.sql         # D1 schema
│   │   └── pages/             # Frontend build
│   └── cli/
│       └── init.py            # `npx @mumega/sos init` logic
```

---

*Architecture designed by kasra_0111 | Jan 11, 2026*
