# SOS Tech Radar — Evaluated Technologies

**Keep this updated.** When you find a tool, add it here. Don't evaluate twice.

## ADOPT (Using)

| Tech | What | Why We Use It |
|---|---|---|
| Redis | Message bus + pub/sub + streams | Core of SOS nervous system |
| MCP (Anthropic) | Agent tool protocol | Industry standard, 97M downloads |
| LangGraph | Graph-based workflow orchestration | ToRivers execution engine |
| Celery | Distributed task queue | ToRivers worker execution |
| FastAPI | Python API framework | Squad Service, Dashboard, all services |
| Supabase | PostgreSQL + pgvector | Mirror semantic memory |
| Cloudflare (D1, KV, Workers, Pages) | Edge infrastructure | All web properties + tenant databases |
| OpenClaw | Multi-agent gateway | Free model agents (Gemma, Haiku) |
| Gemma 4 | Open-weight LLM | Free, trainable per-tenant |
| SitePilotAI | WordPress MCP tools | 239 tools, the organism's hands |
| GoHighLevel | CRM + marketing automation | Client lead management |
| Stripe | Payments | Tenant provisioning + billing |
| systemd | Service management | All SOS services |
| tmux | Agent sessions | Claude Code + agent persistence |
| Docker | Containerization | docker-compose for SOS stack |

## TRIAL (Evaluating)

| Tech | What | Status | Notes |
|---|---|---|---|
| Anthropic Managed Agents | Hosted agent execution | Evaluated 2026-04-09 | Validated our architecture. Too expensive ($0.08/hr + per-token). Use for enterprise clients only. |
| A2A Protocol (Google/Microsoft) | Cross-cloud agent communication | Not started | Would let us talk to Microsoft/Google agents. Future. |
| HNSW Indexing | Faster vector search | Not started | 150-12,500x faster than flat. Mirror upgrade candidate. |

## HOLD (Not Now, Maybe Later)

| Tech | What | Why Hold |
|---|---|---|
| n8n | Workflow automation | ToRivers does this already with LangGraph |
| Kubernetes | Container orchestration | Overkill until 50+ tenants. Linux users work for now. |
| Temporal | Durable workflow engine | LangGraph + Celery sufficient for now |
| OAuth 2.1 MCP Gateway | Enterprise-grade MCP auth | Token auth works. OAuth when we have enterprise clients. |

## SKIP (Evaluated, Not Needed)

| Tech | What | Why Skip | Date |
|---|---|---|---|
| RTK (rtk-ai/rtk) | Token compression for CLI | We're on Claude Max (flat rate) + Gemma (free). No per-token cost to optimize. | 2026-04-09 |
| CrewAI (as primary) | Role-based multi-agent | Built adapter but LangGraph wins for production. CrewAI for prototyping only. | 2026-04-08 |

## WATCH (Interesting, Monitor)

| Tech | What | Why Watch |
|---|---|---|
| Goose (Block/Jack Dorsey) | Open-source agent, Apache 2.0 | Validates market. Study their install UX. |
| Sierra AI | Customer support agents ($150M revenue) | Vertical agent success story. Study their GTM. |
| Devin (Cognition) | Autonomous software engineer | Competitor for dev agent vertical. |
| Google Agent Space | Marketplace for agents | Similar to ToRivers concept. Watch their marketplace model. |
| Pinecone (100K namespaces) | Vector DB for multi-tenant | Mirror alternative if Supabase pgvector hits limits. |

---

**How to use this:**
- Found a new tool? Add it to WATCH first.
- Evaluated it? Move to ADOPT, TRIAL, HOLD, or SKIP with reason.
- Don't re-evaluate. Check this file first.

## NEW ADDITIONS

### WATCH
| Tech | What | Why |
|---|---|---|
| [Unsloth Studio](https://colab.research.google.com/github/unslothai/unsloth/blob/main/studio/Unsloth_Studio_Colab.ipynb) | Free Colab for fine-tuning LLMs (Gemma, Llama, etc.) 2x faster, 70% less memory | Mirror → training data → Unsloth fine-tunes Gemma per-tenant. This is the Mirror→Gemma pipeline we've been talking about. Free on Colab. |
| Cloudflare Workers AI fine-tuning | Fine-tune Gemma via AutoTrain, deploy on Workers AI | FREE. Already on our account. Gemma 4 available. AutoTrain handles training. THIS IS THE PIPELINE. |
| HuggingFace AutoTrain | Upload dataset, fine-tune any model | Free tier. API available. Feeds into CF Workers AI. |
| Google AI Studio fine-tuning | Tune Gemma in browser | Free but manual. Backup option. |
| Kaggle Notebooks | 30hr/week free GPU | API-triggered. Backup for heavy training. |
