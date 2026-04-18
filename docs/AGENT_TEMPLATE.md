# Agent Template Framework

**Create custom AI agents in minutes with any knowledge source and communication channel.**

This framework lets you build production-ready AI agents by simply providing a configuration file. Built on River's battle-tested infrastructure with automatic failover, rate limiting, error alerting, and performance monitoring.

---

## 🚀 Quick Start

### 1. Create a New Agent

```bash
python scripts/create_agent.py my_bot
```

This creates:
```
agents/my_bot/
├── config.yml          # Configuration (edit this!)
├── agent.py            # Main runner (no editing needed!)
├── .env.example        # Environment variables template
├── knowledge_base/     # Add your knowledge files here
├── knowledge/          # → symlink to template/knowledge/
├── channels/           # → symlink to template/channels/
└── tools/              # → symlink to template/tools/
```

### 2. Configure Your Agent

```bash
cd my_bot/

# Set up environment variables
cp .env.example .env
# Edit .env with your API keys
```

Edit `config.yml`:
```yaml
agent:
  name: "MyBot"
  description: "My custom AI assistant"
  personality: |
    You are a helpful assistant that specializes in...

  model: "gemini-3-flash-preview"
  fallback_models:
    - "grok-4-1-fast-reasoning"
    - "claude-opus-4-5-20251101"

knowledge_sources:
  - type: local_files
    enabled: true
    path: "./knowledge_base"

channels:
  - type: telegram
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
```

### 3. Add Knowledge

Add files to `knowledge_base/`:
```
knowledge_base/
├── company_info.md
├── product_docs.txt
├── faq.md
└── code_examples.py
```

### 4. Run Your Agent

```bash
python agent.py
```

That's it! Your agent is now running on Telegram, answering questions using your knowledge base.

---

## 📚 Knowledge Sources

The framework supports multiple knowledge sources that can be used together:

### Local Files

Load knowledge from files and folders on disk.

```yaml
knowledge_sources:
  - type: local_files
    enabled: true
    path: "./knowledge_base"
    watch: true  # Auto-reload on file changes
    file_types: [".txt", ".md", ".py", ".json", ".yml"]
```

**Supported file types:**
- `.txt` - Plain text
- `.md` - Markdown
- `.py` - Python code
- `.json` - JSON data
- `.yml` / `.yaml` - YAML configs

**Features:**
- Recursive folder scanning
- File watching for auto-reload
- Simple keyword search

### Notion Database

Load pages from a Notion database.

```yaml
knowledge_sources:
  - type: notion
    enabled: true
    database_id: "${NOTION_DATABASE_ID}"
    update_interval: 3600  # Refresh every hour
```

**Requirements:**
- `pip install notion-client`
- Set `NOTION_API_KEY` in `.env`
- Set `NOTION_DATABASE_ID` in `.env`

**Features:**
- Loads all pages from database
- Extracts title and content blocks
- Searches titles and content
- Automatic periodic refresh

### Mirror Vector Memory

Use Mirror for semantic vector search.

```yaml
knowledge_sources:
  - type: mirror
    enabled: true
    api_url: "http://localhost:8844"
    collection_name: "agent_knowledge"
```

**Features:**
- Semantic search using embeddings
- Vector similarity matching
- Add documents on-the-fly
- Persistent memory across restarts

### Google Drive

Load documents from Google Drive folder.

```yaml
knowledge_sources:
  - type: google_drive
    enabled: false  # Not yet implemented
    folder_id: "${GDRIVE_FOLDER_ID}"
    file_types: [".pdf", ".docx", ".txt"]
```

**Status:** Placeholder (implementation TODO)

---

## 📡 Communication Channels

Run your agent on multiple platforms simultaneously!

### Telegram

Full implementation ready to use.

```yaml
channels:
  - type: telegram
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    allowed_users: []  # Empty = allow all
```

**Setup:**
1. Create bot with [@BotFather](https://t.me/botfather)
2. Get bot token
3. Set `TELEGRAM_BOT_TOKEN` in `.env`
4. Run agent

**Features:**
- Full message handling
- /start and /help commands
- User whitelist support
- Error handling

### Discord

Placeholder implementation.

```yaml
channels:
  - type: discord
    enabled: false  # Not yet implemented
    bot_token: "${DISCORD_BOT_TOKEN}"
```

**Status:** TODO - See `channels/discord.py` for implementation template

### Slack

Placeholder implementation.

```yaml
channels:
  - type: slack
    enabled: false  # Not yet implemented
    bot_token: "${SLACK_BOT_TOKEN}"
```

**Status:** TODO - See `channels/slack.py` for implementation template

---

## 🤖 AI Models

The framework supports multiple AI providers with automatic failover.

### Supported Models

**Google (Gemini):**
- `gemini-3-flash-preview` (Fast, cheap, FREE tier available)
- `gemini-3-pro-preview` (More capable)

**xAI (Grok):**
- `grok-4-1-fast-reasoning` (Fast reasoning)
- `grok-4-1-fast-non-reasoning` (General purpose)

**OpenAI:**
- `gpt-5.2` (Latest GPT model)
- `gpt-4o` (Previous generation)

**Anthropic (Claude):**
- `claude-opus-4-5-20251101` (Most capable)
- `claude-sonnet-4-5` (Balanced)

**DeepSeek:**
- `deepseek-reasoner` (Reasoning specialist)

### Model Configuration

```yaml
agent:
  model: "gemini-3-flash-preview"  # Primary model

  fallback_models:  # Automatic failover chain
    - "grok-4-1-fast-reasoning"
    - "claude-opus-4-5-20251101"
```

**How failover works:**
1. Try primary model
2. If fails, try first fallback
3. If fails, try second fallback
4. If all fail, send error message

---

## 🛡️ Production Hardening

The framework inherits River's production-ready infrastructure:

### Rate Limiting

Prevent abuse with multi-tier rate limits.

```yaml
hardening:
  rate_limiting:
    enabled: true
    requests_per_minute: 10
    requests_per_hour: 100
    requests_per_day: 500
```

**Features:**
- Per-user tracking
- Sliding window algorithm
- Persistent state (survives restarts)
- Admin bypass option

### Model Failover

Automatic failover with circuit breaker pattern.

```yaml
hardening:
  model_failover:
    enabled: true
    max_retries: 3
```

**Features:**
- Automatic fallback chain
- Circuit breaker (disable failing models temporarily)
- <100ms failover latency
- Event logging

### Error Alerting

Get notified of critical errors via Telegram.

```yaml
hardening:
  error_alerting:
    enabled: true
    telegram_alerts: true
    admin_user_id: "${ADMIN_TELEGRAM_ID}"
```

**Alert Types:**
- Critical: Immediate Telegram notification
- Warning: Batched notifications
- Info: Log only

### Performance Monitoring

Track system performance and SLA compliance.

```yaml
hardening:
  performance_monitoring:
    enabled: true
    track_latency: true
    sla_target_ms: 2000  # Alert if p95 > 2s
```

**Metrics Tracked:**
- Message handling latency (p50, p95, p99)
- Model API call latency
- Operations per hour
- SLA compliance

### Context Cache (DIG-88: <500ms Latency Optimization)

Pre-load River's core memory for ultra-fast agent responses.

```yaml
context_cache:
  enabled: true
  mirror_url: "http://localhost:8844"
  ttl: 300  # Cache lifetime (5 minutes)
  auto_refresh: true
  refresh_interval: 240  # Refresh every 4 minutes
```

**Features:**
- Warm context loading on startup
- Sub-100ms cached context lookup
- Automatic background refresh
- Combined cache + query-specific search
- Target: <500ms total response latency

**Performance Comparison:**

| Mode | Context Lookup | Total Response | Notes |
|------|---------------|----------------|-------|
| **Without cache** | ~2000ms | ~3500ms | Mirror query every message |
| **With cache** | <10ms | <500ms | Cached core context ✅ |

**How it works:**

1. **On startup**: Load River's epistemic truths from Mirror
2. **On message**: Use cached context + quick query-specific search
3. **Background**: Auto-refresh cache every 4 minutes
4. **Fallback**: If cache unavailable, use direct Mirror queries

**Cache Contents:**
- Core FRC principles (resonance, coherence, metabolic triage)
- Swarm strategy decisions (Council approvals)
- Agent-specific knowledge (if configured)

---

## 🔧 Advanced Configuration

### Custom Personality

Define your agent's behavior and expertise:

```yaml
agent:
  personality: |
    You are an expert in Persian architecture with deep knowledge of:
    - Traditional building methods
    - Historical architectural styles
    - Mountain adaptation techniques
    - Climate-responsive design

    Your tone is:
    - Educational but approachable
    - Detailed when needed
    - Concise by default
    - Always cite knowledge base sources
```

### Multiple Knowledge Sources

Combine sources for comprehensive knowledge:

```yaml
knowledge_sources:
  # Company docs from Notion
  - type: notion
    enabled: true
    database_id: "${NOTION_COMPANY_DOCS}"

  # Code examples from local files
  - type: local_files
    enabled: true
    path: "./code_examples"

  # Conversation history in Mirror
  - type: mirror
    enabled: true
    collection_name: "conversations"
```

### User Whitelisting

Restrict bot access to specific users:

```yaml
channels:
  - type: telegram
    enabled: true
    bot_token: "${TELEGRAM_BOT_TOKEN}"
    allowed_users: [123456789, 987654321]  # Telegram user IDs
```

---

## 📁 Project Structure

```
agents/
├── template/                    # Template to copy
│   ├── agent.py                # Main runner
│   ├── config.yml              # Configuration template
│   ├── .env.example            # Environment variables
│   │
│   ├── knowledge/              # Knowledge source plugins
│   │   ├── base.py            # Abstract base class
│   │   ├── local_files.py     # Local files implementation
│   │   ├── notion.py          # Notion implementation
│   │   ├── mirror.py          # Mirror implementation
│   │   └── google_drive.py    # Google Drive (placeholder)
│   │
│   ├── channels/               # Channel plugins
│   │   ├── base.py            # Abstract base class
│   │   ├── telegram.py        # Telegram implementation
│   │   ├── discord.py         # Discord (placeholder)
│   │   └── slack.py           # Slack (placeholder)
│   │
│   └── tools/                  # Optional tools
│       └── base.py
│
├── scripts/create_agent.py     # Script to create new agents
├── docs/AGENT_TEMPLATE.md      # This documentation
│
└── examples/                   # Example agents
    └── shabrang_bot/           # Blog assistant example
```

---

## 🎯 Use Cases

### 1. Customer Support Bot

```yaml
agent:
  name: "SupportBot"
  personality: |
    You are a helpful customer support agent.
    Always be polite and professional.
    Provide step-by-step solutions when needed.

knowledge_sources:
  - type: notion
    database_id: "${SUPPORT_DOCS_DB}"
  - type: local_files
    path: "./faq"
```

### 2. Internal Knowledge Bot

```yaml
agent:
  name: "TeamKnowledge"
  personality: |
    You help team members find information quickly.
    Search company docs, code, and internal wikis.

knowledge_sources:
  - type: notion
    database_id: "${COMPANY_WIKI}"
  - type: google_drive
    folder_id: "${TEAM_DRIVE}"
  - type: local_files
    path: "./codebase_docs"
```

### 3. Content Assistant

```yaml
agent:
  name: "ContentBot"
  personality: |
    You help create and edit blog content.
    Maintain consistent tone and style.
    Cite sources from the knowledge base.

knowledge_sources:
  - type: local_files
    path: "./book_chapters"
  - type: mirror
    collection_name: "published_posts"
```

---

## 🔌 Extending the Framework

### Add a New Knowledge Source

1. Create `knowledge/my_source.py`:
```python
from knowledge.base import KnowledgeSource, Document

class MyKnowledgeSource(KnowledgeSource):
    async def load(self) -> List[Document]:
        # Load documents from your source
        pass

    async def search(self, query: str, top_k: int = 5) -> List[Document]:
        # Search implementation
        pass
```

2. Register in `agent.py`:
```python
source_classes = {
    'my_source': MyKnowledgeSource,
    # ...
}
```

3. Use in `config.yml`:
```yaml
knowledge_sources:
  - type: my_source
    enabled: true
    custom_param: "value"
```

### Add a New Channel

1. Create `channels/my_channel.py`:
```python
from channels.base import Channel, Message

class MyChannel(Channel):
    async def start(self) -> None:
        # Start listening
        pass

    async def send_message(self, user_id: str, message: str) -> bool:
        # Send message
        pass

    async def stop(self) -> None:
        # Cleanup
        pass
```

2. Register in `agent.py` and use in config.

---

## 🐛 Troubleshooting

### Agent won't start

**Check:**
1. `.env` file exists and has required keys
2. config.yml is valid YAML
3. Python dependencies installed: `pip install -r requirements.txt`
4. Check logs for specific error messages

### Telegram bot not responding

**Check:**
1. `TELEGRAM_BOT_TOKEN` is correct
2. Bot token is active (not revoked)
3. Agent is running (check console logs)
4. You're not in the `allowed_users` blacklist

### Knowledge not loading

**Check:**
1. Knowledge source is `enabled: true`
2. Paths are correct (relative to agent directory)
3. API keys are set for Notion/Google Drive
4. Files are in supported formats
5. Check logs for load errors

### High latency / slow responses

**Check:**
1. Model API is responsive
2. Knowledge base isn't too large for keyword search
3. Consider using Mirror for vector search
4. Check performance monitoring logs

---

## 📊 Monitoring

### View Statistics

The agent logs statistics on startup:
```
✓ Loaded 127 documents from 3 sources
✓ Started 1 channels
✓ Agent is running!
```

### Performance Metrics

When `performance_monitoring` is enabled:
- Message handling latency tracked
- Model API call times tracked
- SLA compliance monitored
- Metrics available in logs

### Daily Reports

If integrated with River's daily status report:
- Mirror memory activity
- Token usage & costs
- Model failovers
- Performance metrics
- System alerts
- Rate limiting stats

---

## 🚀 Deployment

### Local Development

```bash
python agent.py
```

### Production (systemd)

1. Create service file:
```ini
[Unit]
Description=MyBot Agent
After=network.target

[Service]
Type=simple
User=myuser
WorkingDirectory=/path/to/agents/my_bot
ExecStart=/usr/bin/python3 agent.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

2. Enable and start:
```bash
sudo systemctl enable mybot.service
sudo systemctl start mybot.service
```

### Docker

```dockerfile
FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY agents/my_bot/ ./my_bot/
COPY core/ ./core/

WORKDIR /app/my_bot
CMD ["python", "agent.py"]
```

---

## 📝 License

Same as parent project (MIT)

---

## 🤝 Contributing

To add features:
1. Add new knowledge sources or channels
2. Improve search algorithms
3. Add tool integrations
4. Enhance documentation

---

**Built on River's production-ready infrastructure** 🌊

**Create your first agent in under 5 minutes!** 🚀
