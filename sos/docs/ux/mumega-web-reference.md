# Mumega-Web Dashboard Reference

Quick reference for existing dashboard features that SOS can leverage.

## Current Route Structure

```
/dashboard
├── /                    # Neural Link (main chat)
├── /research            # Research/scouting
├── /creator             # Agent builder (Forge Soul)
├── /swarm               # Multi-agent coordination
├── /profile             # Agent stats & 16D radar
├── /settings            # Connectors & identity
└── /admin
    ├── /                # Sovereign Command overview
    ├── /work            # Tasks & Linear integration
    ├── /mirror          # Memory/vector DB
    ├── /mcp             # MCP tool hub
    ├── /economy         # $MIND token stats
    ├── /scouts          # Scout intelligence
    ├── /strata          # Low-level tools
    ├── /canvas          # Visual builder
    └── /console         # Terminal
```

## Key Components

| Component | Location | Purpose |
|-----------|----------|---------|
| `DashboardLayout` | `/components/DashboardLayout.tsx` | Master layout |
| `DashboardSidebar` | `/components/DashboardSidebar.tsx` | Left navigation |
| `SovereignVitals` | `/components/admin/SovereignVitals.tsx` | System health |
| `ConnectorCatalog` | `/components/admin/ConnectorCatalog.tsx` | Integrations |
| `AgentSwitcher` | `/components/AgentSwitcher.tsx` | Agent selection |
| `MCPToolbar` | `/components/MCPToolbar.tsx` | Tool management |

## UI Library (Shadcn)

Located in `/components/ui/`:
- `button`, `card`, `badge`, `dialog`
- `input`, `select`, `slider`, `switch`
- `table`, `tabs`, `toast`
- `resizable` (panel groups)

## State Management

**Zustand Store** (`/lib/store.ts`):
```typescript
interface AppState {
  mode: 'SIMPLE' | 'GEEKY' | 'RESEARCH';
  activeAgentId: string | null;
  setMode: (mode) => void;
  setActiveAgent: (id) => void;
}
```

## Auth Pattern

**Supabase SSR** (`/lib/supabase/`):
- `client.ts` - Browser client
- `middleware.ts` - Session validation
- Optional auth (graceful degradation)

**Identity Resolution**:
1. Supabase user ID
2. localStorage `mumega.sovereign_user_id`
3. Anonymous fallback

## API Route Pattern

```typescript
// /app/api/example/route.ts
import { NextRequest, NextResponse } from 'next/server';

export async function GET(request: NextRequest) {
  try {
    // Fetch from backend service
    const res = await fetch('http://127.0.0.1:8010/endpoint');
    const data = await res.json();
    return NextResponse.json(data);
  } catch (error) {
    return NextResponse.json(
      { error: 'Service unavailable' },
      { status: 503 }
    );
  }
}
```

## Backend Services

| Service | Port | Purpose |
|---------|------|---------|
| Core | 8010 | Main brain |
| Bridge | 8001 | Economy/API |
| Mirror | 8844 | Memory system |
| n8n | 5678 | Workflows |

## Fetch Pattern (Client)

```typescript
const [data, setData] = useState(null);
const [loading, setLoading] = useState(true);

useEffect(() => {
  fetch('/api/endpoint')
    .then(res => res.json())
    .then(setData)
    .catch(console.error)
    .finally(() => setLoading(false));
}, []);
```

## Color Scheme

- Primary: `cyan-400/500` (neural link)
- Admin: `green-400` (orchestration)
- Tools: `purple-400` (low-level)
- Background: `black` / `zinc-900`
- Text: `white` / `white/60`

## Quick Start for SOS

1. Copy `/app/dashboard/admin/page.tsx` as template
2. Create `/app/sos/layout.tsx` with custom sidebar
3. Reuse `Card`, `Badge`, `Table` from `/components/ui/`
4. Add API routes at `/app/api/sos/`
5. Extend Zustand store with SOS slice
