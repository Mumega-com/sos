# Project Scaffold — What each project directory needs

Every customer project should have this structure:

```
project-name/
  .claude/
    settings.json          ← MCP servers for this project
    commands/              ← project-specific commands  
    rules/                 ← project-specific rules
  .env                     ← project secrets (gitignored)
  CLAUDE.md                ← project entry point for agents
```

## .claude/settings.json

```json
{
  "mcpServers": {
    "sos": {
      "type": "stdio",
      "command": "node",
      "args": ["~/sos-remote.js"],
      "env": {
        "SOS_TOKEN": "sk-bus-{slug}-xxx",
        "MIRROR_TOKEN": "sk-mumega-{slug}-xxx",
        "AGENT": "{slug}"
      }
    }
  }
}
```

Additional MCPs per project type:

- **WordPress project**: SitePilot MCP (wp-json endpoint)
- **Cloudflare project**: wrangler (already global)
- **GitHub project**: gh CLI (already global)
- **Supabase project**: supabase CLI or direct API

## CLAUDE.md (per project)

```markdown
# {Project Name}

## Stack
- [tech stack details]

## Quick Start
- [how to run locally]

## Operations
- Run `/hand {slug}` to load full project context
- Active operation: {product} (schedule: {cron})

## SOPs
- Deploy: `/sop deploy`
- Incident: `/sop incident`
```

## .env (per project, gitignored)

```
SPAI_API_KEY=spai_xxx              # SitePilot (WordPress)
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_ANON_KEY=xxx
WORDPRESS_URL=https://customer-site.com
```
