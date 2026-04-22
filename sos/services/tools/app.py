"""
SOS Tools Service API.

Provides tool registry, MCP server management, plugin management,
and tool execution endpoints.
"""

import json
import os
import subprocess
import sys
import time
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from sos import __version__
from sos.observability.logging import clear_context, get_logger, set_agent_context
from sos.observability.metrics import MetricsRegistry, render_prometheus
from sos.observability.tracing import SPAN_ID_HEADER, TRACE_ID_HEADER, TraceContext
from sos.services.tools.core import ToolsCore
from sos.services.tools.governance import enforce_mcp_register, enforce_tool_execute

log = get_logger("tools_app")
SERVICE_NAME = "tools"
_START_TIME = time.time()

metrics = MetricsRegistry()
REQUEST_COUNT = metrics.counter(
    name="sos_requests_total",
    description="Total requests",
    label_names=("service", "status"),
)
REQUEST_DURATION = metrics.histogram(
    name="sos_request_duration_seconds",
    description="Request duration",
    label_names=("service",),
)

app = FastAPI(title="SOS Tools Service", version=__version__)
tools = ToolsCore()

# ============================================================
# In-memory registries (for MCP servers and plugins)
# ============================================================

_MCP_SERVERS: Dict[str, Dict[str, Any]] = {}
_MCP_SERVER_TOOLS: Dict[str, List[Dict[str, Any]]] = {}

_PLUGINS: Dict[str, Dict[str, Any]] = {}
_PLUGIN_TOOLS: Dict[str, Dict[str, Any]] = {}
_PLUGIN_SIGNATURE_VERIFIED: Dict[str, bool] = {}
_PLUGIN_POLICY_RESULTS: Dict[str, Dict[str, Any]] = {}


@app.middleware("http")
async def _observability_middleware(request: Request, call_next):
    ctx = TraceContext.from_headers(dict(request.headers))
    ctx.activate()

    if agent_id := request.headers.get("X-SOS-Agent-ID"):
        set_agent_context(agent_id)

    status_label = "success"
    with REQUEST_DURATION.labels(service=SERVICE_NAME).time():
        try:
            response = await call_next(request)
            status_label = "success" if response.status_code < 400 else "error"
        except Exception as e:
            status_label = "error"
            log.error("Unhandled exception", error=str(e), path=str(request.url.path))
            response = JSONResponse(status_code=500, content={"detail": "internal_error"})

    REQUEST_COUNT.labels(service=SERVICE_NAME, status=status_label).inc()
    response.headers[TRACE_ID_HEADER] = ctx.trace_id
    response.headers[SPAN_ID_HEADER] = ctx.span_id
    clear_context()
    return response

# ============================================================
# Request/Response Models
# ============================================================


class ToolRequest(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]


class ExecuteRequest(BaseModel):
    arguments: Dict[str, Any]


class MCPServerRegister(BaseModel):
    name: str
    url: str
    transport: str = "http"


class MCPDiscoverRequest(BaseModel):
    tools: List[Dict[str, Any]]


class PluginInstallRequest(BaseModel):
    cid: str


# ============================================================
# Health & Basic Endpoints
# ============================================================


@app.get("/health")
async def health():
    """Health check."""
    return {
        "status": "ok",
        "version": __version__,
        "service": SERVICE_NAME,
        "uptime_seconds": time.time() - _START_TIME,
    }


@app.get("/metrics")
async def metrics_endpoint():
    """Prometheus metrics endpoint."""
    return PlainTextResponse(
        render_prometheus(metrics),
        media_type="text/plain; version=0.0.4",
    )


@app.get("/list")
async def list_tools_legacy():
    """Legacy list endpoint."""
    return await tools.list_tools()


@app.post("/execute")
async def execute_tool(req: ToolRequest, request: Request):
    """Execute a tool by name."""
    await enforce_tool_execute(request, req.tool_name)
    try:
        result = await tools.execute(req.tool_name, req.arguments)
        return {"status": "success", "output": result}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# Tools Registry Endpoints
# ============================================================


@app.get("/tools")
async def get_tools():
    """List all available tools (local + MCP + plugin)."""
    all_tools = await tools.list_tools()

    # Add MCP server tools
    for server_name, server_tools in _MCP_SERVER_TOOLS.items():
        for tool in server_tools:
            tool_def = {
                "name": tool["name"],
                "description": tool.get("description", ""),
                "category": tool.get("category", "custom"),
                "parameters": tool.get("parameters", {}),
                "returns": tool.get("returns", ""),
                "metadata": {"provider": "mcp", "server": server_name},
            }
            all_tools.append(tool_def)

    # Add plugin tools
    for tool_name, tool_info in _PLUGIN_TOOLS.items():
        tool_def = {
            "name": tool_name,
            "description": tool_info.get("description", "Plugin tool"),
            "category": tool_info.get("category", "custom"),
            "parameters": tool_info.get("parameters", {}),
            "returns": tool_info.get("returns", ""),
            "metadata": {"provider": "plugin", "plugin": tool_info.get("plugin")},
        }
        all_tools.append(tool_def)

    return all_tools


@app.get("/tools/{tool_name}")
async def get_tool(tool_name: str):
    """Get a specific tool by name."""
    # Check MCP tools
    for server_name, server_tools in _MCP_SERVER_TOOLS.items():
        for tool in server_tools:
            if tool["name"] == tool_name:
                return {
                    **tool,
                    "metadata": {"provider": "mcp", "server": server_name},
                }

    # Check plugin tools
    if tool_name in _PLUGIN_TOOLS:
        tool_info = _PLUGIN_TOOLS[tool_name]
        return {
            "name": tool_name,
            "description": tool_info.get("description", "Plugin tool"),
            "category": tool_info.get("category", "custom"),
            "parameters": tool_info.get("parameters", {}),
            "returns": tool_info.get("returns", ""),
            "metadata": {"provider": "plugin", "plugin": tool_info.get("plugin")},
        }

    # Check local tools
    local_tools = await tools.list_tools()
    for tool in local_tools:
        if tool.get("name") == tool_name:
            return tool

    raise HTTPException(status_code=404, detail="tool_not_found")


@app.post("/tools/{tool_name}/execute")
async def execute_specific_tool(tool_name: str, req: ExecuteRequest, request: Request):
    """Execute a specific tool."""
    await enforce_tool_execute(request, tool_name)
    # Check if plugin execution is enabled
    if tool_name.startswith("plugin."):
        tools_enabled = os.getenv("SOS_TOOLS_EXECUTION_ENABLED", "").lower() in (
            "1",
            "true",
            "yes",
        )
        plugins_enabled = os.getenv("SOS_PLUGINS_EXECUTION_ENABLED", "").lower() in (
            "1",
            "true",
            "yes",
        )

        if not (tools_enabled and plugins_enabled):
            raise HTTPException(status_code=501, detail="plugin_execution_not_enabled")

        # Execute plugin tool
        return await _execute_plugin_tool(tool_name, req.arguments)

    # Execute normal tool
    try:
        result = await tools.execute(tool_name, req.arguments)
        if isinstance(result, str) and result.startswith("Search Error: No module named"):
            raise HTTPException(status_code=501, detail="tool_execution_not_implemented")
        return {"success": True, "output": result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================
# MCP Server Management Endpoints
# ============================================================


@app.get("/mcp/servers")
async def list_mcp_servers():
    """List registered MCP servers."""
    return list(_MCP_SERVERS.values())


@app.post("/mcp/servers")
async def register_mcp_server(req: MCPServerRegister, request: Request):
    """Register an MCP server."""
    await enforce_mcp_register(request, req.name)
    server = {
        "name": req.name,
        "url": req.url,
        "transport": req.transport,
        "status": "registered",
    }
    _MCP_SERVERS[req.name] = server
    _MCP_SERVER_TOOLS[req.name] = []
    log.info(f"Registered MCP server: {req.name} at {req.url}")
    return server


@app.delete("/mcp/servers/{server_name}")
async def unregister_mcp_server(server_name: str, request: Request):
    """Unregister an MCP server."""
    await enforce_mcp_register(request, server_name)
    if server_name not in _MCP_SERVERS:
        raise HTTPException(status_code=404, detail="server_not_found")

    del _MCP_SERVERS[server_name]
    _MCP_SERVER_TOOLS.pop(server_name, None)
    log.info(f"Unregistered MCP server: {server_name}")
    return {"deleted": True, "name": server_name}


@app.post("/mcp/servers/{server_name}/discover")
async def discover_mcp_tools(server_name: str, req: MCPDiscoverRequest):
    """
    Discover/register tools from an MCP server.

    In manual mode (SOS_MCP_DISCOVERY_MODE=manual), tools must be provided in request.
    In auto mode, would query the server for available tools.
    """
    if server_name not in _MCP_SERVERS:
        raise HTTPException(status_code=404, detail="server_not_found")

    discovery_mode = os.getenv("SOS_MCP_DISCOVERY_MODE", "auto")

    if discovery_mode == "manual":
        if not req.tools:
            raise HTTPException(status_code=400, detail="missing_tools")

        # Register provided tools
        registered_tools = []
        for tool in req.tools:
            tool_name = f"mcp.{server_name}.{tool['name']}"
            tool_def = {
                "name": tool_name,
                "description": tool.get("description", ""),
                "category": tool.get("category", "custom"),
                "parameters": tool.get("parameters", {}),
                "returns": tool.get("returns", ""),
            }
            registered_tools.append(tool_def)

        _MCP_SERVER_TOOLS[server_name] = registered_tools
        log.info(f"Discovered {len(registered_tools)} tools from MCP server: {server_name}")
        return registered_tools

    # Auto mode: would query the server
    # For now, just return empty list
    return _MCP_SERVER_TOOLS.get(server_name, [])


# ============================================================
# Plugin Management Endpoints
# ============================================================


@app.get("/plugins")
async def list_plugins():
    """List installed plugins."""
    return list(_PLUGINS.values())


@app.post("/plugins")
async def install_plugin(req: PluginInstallRequest):
    """
    Install a plugin from artifact registry by CID.

    Steps:
    1. Resolve CID to local path via ArtifactRegistry
    2. Load plugin.json manifest
    3. Run policy checks if configured
    4. Register plugin tools
    """
    from sos.artifacts.registry import ArtifactRegistry

    registry = ArtifactRegistry()

    try:
        # Resolve CID to path
        try:
            registry.get(req.cid)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="artifact_not_found")

        plugin_path = registry.artifact_dir(req.cid) / "files"
        manifest_path = plugin_path / "plugin.json"

        if not manifest_path.exists():
            raise HTTPException(status_code=400, detail="invalid_plugin_no_manifest")

        manifest = json.loads(manifest_path.read_text())
        plugin_name = manifest["name"]

        # Run policy checks
        policy_result = await _run_plugin_policy(plugin_path, manifest)
        if not policy_result["passed"]:
            _PLUGIN_POLICY_RESULTS[req.cid] = policy_result
            raise HTTPException(status_code=403, detail="plugin_policy_failed")

        _PLUGIN_POLICY_RESULTS[req.cid] = policy_result

        # Load tools
        tools_path = plugin_path / manifest.get("entrypoints", {}).get("tools", "tools.json")
        plugin_tools = []
        if tools_path.exists():
            tool_defs = json.loads(tools_path.read_text())
            for tool in tool_defs:
                tool_name = f"plugin.{plugin_name}.{tool['name']}"
                _PLUGIN_TOOLS[tool_name] = {
                    **tool,
                    "plugin": plugin_name,
                    "cid": req.cid,
                    "path": str(plugin_path),
                    "execute": manifest.get("entrypoints", {}).get("execute"),
                }
                plugin_tools.append(tool_name)

        # Register plugin
        _PLUGINS[req.cid] = {
            "cid": req.cid,
            "name": plugin_name,
            "version": manifest.get("version", "0.0.0"),
            "tools": plugin_tools,
            "path": str(plugin_path),
        }

        log.info(f"Installed plugin: {plugin_name} ({req.cid})")

        return {
            "cid": req.cid,
            "name": plugin_name,
            "version": manifest.get("version"),
            "tools": plugin_tools,
            "policy": policy_result,
        }

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Plugin install failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/plugins/{cid}")
async def uninstall_plugin(cid: str):
    """Uninstall a plugin by CID."""
    if cid not in _PLUGINS:
        raise HTTPException(status_code=404, detail="plugin_not_found")

    plugin = _PLUGINS[cid]

    # Remove plugin tools
    for tool_name in plugin.get("tools", []):
        _PLUGIN_TOOLS.pop(tool_name, None)

    # Remove plugin
    del _PLUGINS[cid]
    _PLUGIN_POLICY_RESULTS.pop(cid, None)
    _PLUGIN_SIGNATURE_VERIFIED.pop(cid, None)

    log.info(f"Uninstalled plugin: {plugin.get('name')} ({cid})")
    return {"deleted": True, "cid": cid}


# ============================================================
# Helper Functions
# ============================================================


async def _run_plugin_policy(plugin_path, manifest) -> Dict[str, Any]:
    """
    Run policy checks on a plugin.

    Uses SOS_PLUGINS_POLICY_COMMANDS env var for custom checks.
    Returns dict with 'passed' bool and 'results'.
    """
    policy_commands = os.getenv("SOS_PLUGINS_POLICY_COMMANDS", "")

    if not policy_commands:
        return {"passed": True, "results": []}

    results = []
    all_passed = True

    for cmd in policy_commands.split(";"):
        cmd = cmd.strip()
        if not cmd:
            continue

        # Replace 'python ' with actual python executable for portability
        if cmd.startswith("python "):
            cmd = sys.executable + cmd[6:]

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                cwd=str(plugin_path),
                capture_output=True,
                text=True,
                timeout=30,
            )
            passed = result.returncode == 0
            results.append({
                "command": cmd,
                "passed": passed,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
            })
            if not passed:
                all_passed = False
        except subprocess.TimeoutExpired:
            results.append({
                "command": cmd,
                "passed": False,
                "error": "timeout",
            })
            all_passed = False
        except Exception as e:
            results.append({
                "command": cmd,
                "passed": False,
                "error": str(e),
            })
            all_passed = False

    enforce = os.getenv("SOS_PLUGINS_POLICY_ENFORCE", "true").lower() in ("1", "true", "yes")

    return {
        "passed": all_passed or not enforce,
        "enforced": enforce,
        "results": results,
    }


async def _execute_plugin_tool(tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a plugin tool."""
    if tool_name not in _PLUGIN_TOOLS:
        raise HTTPException(status_code=404, detail="plugin_tool_not_found")

    tool_info = _PLUGIN_TOOLS[tool_name]
    execute_entry = tool_info.get("execute", "")
    plugin_path = tool_info.get("path", "")

    if not execute_entry:
        raise HTTPException(status_code=500, detail="plugin_missing_entrypoint")

    # Parse entrypoint (e.g., "python:run_tool.py")
    if ":" in execute_entry:
        executor, script = execute_entry.split(":", 1)
    else:
        executor = "python"
        script = execute_entry

    # Use sys.executable for python to ensure correct interpreter
    if executor in ("python", "python3"):
        executor = sys.executable

    script_path = os.path.join(plugin_path, script)

    if not os.path.exists(script_path):
        raise HTTPException(status_code=500, detail="plugin_script_not_found")

    # Extract original tool name (without plugin.name. prefix)
    parts = tool_name.split(".")
    original_name = parts[-1] if len(parts) > 2 else tool_name

    # Execute the script
    request_json = json.dumps({
        "tool_name": original_name,
        "arguments": arguments,
    })

    try:
        result = subprocess.run(
            [executor, script_path],
            input=request_json,
            capture_output=True,
            text=True,
            timeout=30,
            cwd=plugin_path,
        )

        if result.returncode != 0:
            return {"success": False, "error": result.stderr}

        output = json.loads(result.stdout)
        return output

    except subprocess.TimeoutExpired:
        return {"success": False, "error": "execution_timeout"}
    except json.JSONDecodeError:
        return {"success": False, "error": "invalid_output", "raw": result.stdout}
    except Exception as e:
        return {"success": False, "error": str(e)}
