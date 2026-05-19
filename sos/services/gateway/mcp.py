"""
SOS MCP Gateway - Model Context Protocol Server

Provides MCP interface for external AI agents with:
- OAuth 2.1 authentication
- MCP tools for SOS services
- MCP resources for data access
- SSE transport

Ported from CLI sovereign_server.py to use SOS services.
"""

import os
import json
import time
import secrets
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from sos.kernel import Config
from sos.clients.engine import EngineClient
from sos.clients.memory import MemoryClient
from sos.contracts.engine import ChatRequest as EngineChatRequest
from sos.observability.logging import get_logger

# Optional MCP SDK import
try:
    from mcp.server.fastmcp import FastMCP, Context
    HAS_MCP = True
except ImportError:
    HAS_MCP = False
    FastMCP = None
    Context = None

log = get_logger("gateway_mcp")

# Configuration
config = Config.load()
ADMIN_PASSWORD = os.getenv("SOS_ADMIN_PASSWORD", "")
BASE_URL = os.getenv("SOS_MCP_BASE_URL", "https://mcp.mumega.com")
ISSUER = BASE_URL

# OAuth stores (in-memory for MVP)
AUTH_CODES: Dict[str, Dict[str, Any]] = {}
ACCESS_TOKENS: Dict[str, Dict[str, Any]] = {}

# Clients
engine_client = EngineClient(config.engine_url)
memory_client = MemoryClient(config.memory_url)

# Data paths
DATA_DIR = config.paths.data_dir / "gateway"
GRAPH_FILE = DATA_DIR / "knowledge_graph.json"
DATA_DIR.mkdir(parents=True, exist_ok=True)


# --- Knowledge Graph ---

class KnowledgeGraph:
    """Simple knowledge graph for MCP resources."""

    def __init__(self, path: Path):
        self.path = path
        self.nodes: Dict[str, Dict] = {}
        self.edges: list = []
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                self.nodes = data.get("nodes", {})
                self.edges = data.get("edges", [])
            except Exception:
                pass

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({
            "nodes": self.nodes,
            "edges": self.edges,
        }, indent=2))

    def update(self, entity: str, description: str, metadata: dict = None):
        key = entity.lower().strip()
        self.nodes[key] = {
            "name": entity,
            "description": description,
            "metadata": metadata or {},
            "updated_at": time.time(),
        }
        self._save()

    def connect(self, source: str, relation: str, target: str):
        edge = {
            "source": source.lower(),
            "relation": relation,
            "target": target.lower(),
        }
        if edge not in self.edges:
            self.edges.append(edge)
            self._save()

    def search(self, query: str) -> list:
        key = query.lower()
        results = []
        if key in self.nodes:
            node = self.nodes[key]
            results.append(f"ENTITY: {node['name']}\nDESC: {node['description']}")
            related = [e for e in self.edges if e['source'] == key or e['target'] == key]
            for r in related:
                results.append(f"RELATION: {r['source']} --[{r['relation']}]--> {r['target']}")
        return results


graph = KnowledgeGraph(GRAPH_FILE)


# --- FastAPI App ---

app = FastAPI(
    title="SOS MCP Gateway",
    description="Model Context Protocol gateway for SOS Sovereign Network",
    version="2.0.0",
)


# --- OAuth 2.1 Endpoints ---

@app.get("/.well-known/openid-configuration")
async def openid_config():
    """OpenID Connect discovery endpoint."""
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{BASE_URL}/oauth/authorize",
        "token_endpoint": f"{BASE_URL}/oauth/token",
        "response_types_supported": ["code"],
        "subject_types_supported": ["public"],
        "id_token_signing_alg_values_supported": ["RS256"],
        "scopes_supported": ["openid", "profile", "mcp"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "claims_supported": ["sub", "iss", "name"],
    }


@app.get("/oauth/authorize", response_class=HTMLResponse)
async def get_authorize(
    request: Request,
    response_type: str,
    client_id: Optional[str] = None,
    redirect_uri: Optional[str] = None,
    state: Optional[str] = None,
    scope: str = "",
):
    """OAuth authorization page."""
    return f"""
    <html>
        <head><title>SOS Sovereign Login</title></head>
        <body style="font-family: sans-serif; background: #0f172a; color: white; display: flex; align-items: center; justify-content: center; height: 100vh;">
            <div style="background: #1e293b; padding: 2rem; border-radius: 8px; box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);">
                <h2 style="color: #38bdf8;">SOS Sovereign Network</h2>
                <p>An AI agent is requesting access.</p>
                <form method="post" action="/oauth/authorize">
                    <input type="hidden" name="client_id" value="{client_id or 'default'}">
                    <input type="hidden" name="redirect_uri" value="{redirect_uri or ''}">
                    <input type="hidden" name="state" value="{state or ''}">
                    <input type="password" name="password" placeholder="Admin Password" style="width: 100%; padding: 0.5rem; margin-bottom: 1rem; border-radius: 4px; border: none;">
                    <button type="submit" style="width: 100%; padding: 0.5rem; background: #0284c7; color: white; border: none; border-radius: 4px; cursor: pointer;">Authorize Access</button>
                </form>
            </div>
        </body>
    </html>
    """


@app.post("/oauth/authorize")
async def post_authorize(
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    state: str = Form(...),
    password: str = Form(...),
):
    """Handle OAuth authorization."""
    if password != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")

    code = secrets.token_hex(16)
    AUTH_CODES[code] = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "expires_at": time.time() + 600,
    }

    sep = "&" if "?" in redirect_uri else "?"
    return RedirectResponse(f"{redirect_uri}{sep}code={code}&state={state}", status_code=302)


@app.post("/oauth/token")
async def post_token(
    grant_type: str = Form(...),
    code: str = Form(None),
    client_id: str = Form(None),
    redirect_uri: str = Form(None),
):
    """Exchange authorization code for access token."""
    if grant_type == "authorization_code":
        auth_data = AUTH_CODES.pop(code, None)
        if not auth_data or auth_data["expires_at"] < time.time():
            raise HTTPException(status_code=400, detail="Invalid or expired code")

        token = f"sk-sos-{secrets.token_hex(20)}"
        ACCESS_TOKENS[token] = {
            "client_id": client_id,
            "created_at": time.time(),
        }
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "mcp",
        }

    raise HTTPException(status_code=400, detail="Unsupported grant type")


@app.get("/")
async def root():
    """Health check."""
    return {
        "name": "SOS MCP Gateway",
        "status": "online",
        "mcp_url": "/mcp/sse" if HAS_MCP else "MCP SDK not installed",
        "oauth_url": "/.well-known/openid-configuration",
    }


# --- MCP Server (if SDK available) ---

if HAS_MCP:
    mcp = FastMCP(
        "SOS Sovereign Network",
        instructions="Interface to the SOS Sovereign Agent Network. Provides tools for chat, memory, and knowledge graph operations.",
    )

    # MCP Resources
    @mcp.resource("sos://knowledge/graph")
    def get_knowledge_graph() -> str:
        """Get the knowledge graph."""
        return json.dumps({"nodes": graph.nodes, "edges": graph.edges}, indent=2)

    @mcp.resource("sos://agents/list")
    def get_agents_list() -> str:
        """List available SOS agents."""
        agents = ["river", "kasra", "mizan", "mumega", "codex", "consultant", "dandan", "shabrang"]
        return json.dumps({"agents": agents})

    # MCP Tools
    @mcp.tool()
    async def chat_with_agent(message: str, agent: str = "river", ctx: Context = None) -> str:
        """Chat with an SOS agent."""
        try:
            request = EngineChatRequest(
                message=message,
                agent_id=f"agent:{agent}",
                memory_enabled=True,
            )
            result = engine_client.chat(request)
            return result.content
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool()
    async def search_memory(query: str, limit: int = 5, ctx: Context = None) -> str:
        """Search SOS memory."""
        try:
            results = await memory_client.search(query=query, limit=limit)
            return json.dumps(results, indent=2)
        except Exception as e:
            return f"Error: {str(e)}"

    @mcp.tool()
    async def search_knowledge(query: str, ctx: Context = None) -> str:
        """Search the knowledge graph."""
        results = graph.search(query)
        return "\n".join(results) if results else "No matches found."

    @mcp.tool()
    async def update_knowledge(entity: str, fact: str, relation_to: Optional[str] = None, ctx: Context = None) -> str:
        """Add or update a fact in the knowledge graph."""
        graph.update(entity, fact)
        if relation_to:
            graph.connect(relation_to, "contains", entity)
        return f"Knowledge updated: {entity}"

    @mcp.tool()
    async def dispatch_task(title: str, description: str, assigned_to: str = "mumega", ctx: Context = None) -> str:
        """Dispatch a task to an SOS agent."""
        task_id = f"TASK-{secrets.token_hex(3).upper()}"
        log.info(f"Task dispatched: {task_id} -> {assigned_to}")
        return json.dumps({
            "status": "dispatched",
            "task_id": task_id,
            "assigned_to": assigned_to,
            "title": title,
        })

    # MCP Prompts
    @mcp.prompt()
    def agent_briefing(agent: str) -> str:
        """Get briefing prompt for an agent."""
        return f"You are {agent}, an agent in the SOS Sovereign Network. Read your knowledge from sos://knowledge/graph"

    # Mount MCP
    mcp_app = mcp.sse_app()
    app.mount("/mcp", mcp_app)


# --- Run ---

def main():
    """Run the MCP gateway."""
    import uvicorn

    port = int(os.environ.get("SOS_MCP_PORT", "6063"))
    host = os.environ.get("SOS_MCP_HOST", "0.0.0.0")

    log.info(f"Starting SOS MCP Gateway on {host}:{port}")
    log.info(f"MCP SDK available: {HAS_MCP}")

    uvicorn.run(
        app,
        host=host,
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
