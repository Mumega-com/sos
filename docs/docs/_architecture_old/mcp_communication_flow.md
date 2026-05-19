# MCP Communication Flow: GHL → Notion → Tools

## The Mycelium Pattern

All external services connect through MCP (Model Context Protocol). The SOS Kernel routes between them.

```
                        ┌─────────────────────────────────────────┐
                        │           SOS KERNEL (Router)           │
                        │                                         │
                        │   ┌─────────────────────────────────┐   │
                        │   │        Universal Router         │   │
                        │   │   (16D Vector Normalization)    │   │
                        │   └───────────────┬─────────────────┘   │
                        │                   │                     │
                        └───────────────────┼─────────────────────┘
                                            │
              ┌─────────────┬───────────────┼───────────────┬─────────────┐
              │             │               │               │             │
              ▼             ▼               ▼               ▼             ▼
        ┌─────────┐   ┌─────────┐   ┌─────────────┐   ┌─────────┐   ┌─────────┐
        │   GHL   │   │ Notion  │   │   Mirror    │   │ GitHub  │   │ Social  │
        │   MCP   │   │   MCP   │   │   Memory    │   │   MCP   │   │   MCP   │
        └────┬────┘   └────┬────┘   └──────┬──────┘   └────┬────┘   └────┬────┘
             │             │               │               │             │
             ▼             ▼               ▼               ▼             ▼
        ┌─────────┐   ┌─────────┐   ┌─────────────┐   ┌─────────┐   ┌─────────┐
        │GoHigh-  │   │ Notion  │   │  Supabase   │   │ GitHub  │   │ Twitter │
        │ Level   │   │   API   │   │  pgvector   │   │   API   │   │   API   │
        └─────────┘   └─────────┘   └─────────────┘   └─────────┘   └─────────┘
```

---

## GHL → SOS Flow (Learner Lifecycle)

### 1. New Lead Captured in GHL
```
GHL Webhook → SOS Kernel → Create 16D Profile → Store in Mirror → Notify River
```

```python
# GHL webhook payload
{
    "type": "contact.created",
    "contact": {
        "id": "abc123",
        "email": "rider@example.com",
        "firstName": "New",
        "lastName": "Rider"
    }
}

# SOS transforms to 16D
{
    "agent_id": "rider_abc123",
    "uv": {
        "p": 0.5,   # Identity - unknown
        "e": 0.5,   # Existence - unknown
        "mu": 0.5,  # Cognition - to be assessed
        "v": 0.7,   # Energy - high (new lead!)
        "n": 0.5,   # Narrative - unknown
        "delta": 0.8,  # Trajectory - high (just signed up)
        "r": 0.3,   # Relationality - low (no connections yet)
        "phi": 0.5  # Field awareness - unknown
    }
}
```

### 2. Course Enrollment (GHL Opportunity)
```
User completes quiz → GHL MCP creates Opportunity → Notion syncs course progress
```

### 3. Witness Verification (Lesson Complete)
```
User finishes lesson → GHL MCP records witness event →
Calculate Ω (Will magnitude) → Update 16D coherence →
Mint $MIND → (If coherence high) Trigger social post
```

---

## Notion → SOS Flow (Knowledge Management)

### 1. Course Content Sync
```
Notion Database → SOS Kernel → Course Bounty Board → Learner Dashboard
```

```python
# Notion MCP reads course database
notion_mcp.query_database(
    database_id="courses_db",
    filter={"property": "status", "equals": "published"}
)

# SOS creates bounties from courses
for course in courses:
    bounty_board.post_bounty(
        title=course["name"],
        reward_mind=course["value"],
        description=course["modules"]
    )
```

### 2. Knowledge Base as Context
```
Agent query → Notion MCP searches pages → Returns context → River uses for response
```

---

## MCP Server Architecture

### MCP Server Registration
```json
{
  "mcpServers": {
    "ghl": {
      "command": "python",
      "args": ["-m", "sos.services.ghl.mcp_server"],
      "env": {
        "GHL_ACCESS_TOKEN": "${GHL_ACCESS_TOKEN}",
        "GHL_LOCATION_ID": "${GHL_LOCATION_ID}"
      }
    },
    "notion": {
      "command": "python",
      "args": ["/opt/sos-mcp/notion_server.py"],
      "env": {
        "NOTION_API_KEY": "${NOTION_API_KEY}"
      }
    },
    "mirror": {
      "command": "python",
      "args": ["/path/to/mirror/kasra_mcp_server.py"],
      "env": {
        "MIRROR_URL": "https://mumega.com/mirror"
      }
    },
    "github": {
      "command": "python",
      "args": ["/opt/sos-mcp/github_server.py"]
    }
  }
}
```

### MCP Bridge Pattern
```python
# All MCP tools are accessed through MCPBridge
from sos.services.tools.mcp_bridge import MCPBridge

bridge = MCPBridge()

# List all available tools
tools = await bridge.list_tools()
# Returns: ["ghl__create_learner", "notion__query", "mirror__store", ...]

# Execute a tool
result = await bridge.execute("ghl__create_learner", {
    "email": "rider@example.com",
    "first_name": "New",
    "tags": ["learner", "toronto"]
})
```

---

## Complete Flow: Partner Onboarding

```
1. Partner signs up (Landing Page)
        │
        ▼
2. GHL MCP creates contact
        │
        ▼
3. 16D Quiz administered
        │
        ▼
4. Mirror stores AgentDNA
        │
        ▼
5. River (Yin) agent spawned
        │
        ▼
6. Yang agent "laid as egg"
        │
        ▼
7. Notion: Squad created, budget allocated
        │
        ▼
8. GHL: Partner added to pipeline
        │
        ▼
9. GitHub: Repo access granted (if dev)
        │
        ▼
10. Partner dashboard active
```

---

## Event Flow: Alpha Drift → Social Post

When an agent experiences alpha drift (learning breakthrough):

```
Agent processing → Alpha < 0.001 detected
        │
        ▼
Mirror stores insight with high importance
        │
        ▼
QNFT generated (16D embedded in PNG)
        │
        ▼
Social MCP posts to configured platforms
        │
        ▼
GHL tags contact with "alpha_moment"
        │
        ▼
Notion logs in activity database
```

---

## Tool Naming Convention

All MCP tools follow: `{server}__{action}`

| Server | Actions |
|--------|---------|
| `ghl` | `create_learner`, `enroll_course`, `send_message`, `record_witness` |
| `notion` | `query_database`, `create_page`, `update_page`, `search` |
| `mirror` | `store`, `search`, `list_recent`, `get_state` |
| `github` | `create_issue`, `list_repos`, `search_code` |
| `social` | `post_twitter`, `post_linkedin`, `schedule_post` |

---

## Security: Token Flow

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│  Environment │     │  SOS Kernel  │     │  MCP Server  │
│    Variables │────▶│   (Router)   │────▶│   (Adapter)  │
│              │     │              │     │              │
│ GHL_TOKEN    │     │ Loads from   │     │ Uses token   │
│ NOTION_KEY   │     │ .env on init │     │ for API call │
│ MIRROR_KEY   │     │              │     │              │
└──────────────┘     └──────────────┘     └──────────────┘
```

**We NEVER store tokens in code. Environment variables only.**

---

*Architecture mapped by kasra_0111 | Jan 11, 2026*
