from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
import importlib
import inspect
import json
import sys
from pathlib import Path
from datetime import datetime, timezone
import os

from sos.kernel import Config
from sos.observability.logging import get_logger

log = get_logger("tools_core")

@dataclass
class ToolDefinition:
    name: str
    description: str
    arguments: Dict[str, Any]

@dataclass
class PluginManifest:
    name: str
    version: str
    author: Optional[str] = None
    description: Optional[str] = None
    trust_level: str = "community"  # core | verified | community | unsigned
    capabilities_required: List[str] = field(default_factory=list)
    capabilities_provided: List[str] = field(default_factory=list)
    entrypoints: Dict[str, Any] = field(default_factory=dict)
    sandbox: Dict[str, Any] = field(default_factory=dict)
    signature: Optional[str] = None
    path: Optional[Path] = None

class ToolExecutor:
    """Abstract base for tool execution."""
    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any: ...

class LocalTools(ToolExecutor):
    """
    Executes sovereign tools (Web Search, Filesystem, Spore Generation).
    """
    def __init__(self):
        self._tools = {
            "web_search": self._web_search,
            "filesystem_read": self._filesystem_read,
            "filesystem_write": self._filesystem_write,
            "bash": self._bash_execute,
            "generate_spore": self._generate_spore,
            "generate_ui_asset": self._generate_ui_asset,
            "google_drive_list": self._google_drive_list,
            "google_sheet_read": self._google_sheet_read,
            "query_library": self._query_library,
        }

    async def _query_library(self, args: Dict[str, Any]) -> str:
        import httpx
        MEMORY_URL = "http://sos-memory:8001" # Internal Docker URL
        query = args.get("query")
        if not query:
            return "Error: Missing query"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(f"{MEMORY_URL}/search", json={"query": query, "limit": 3})
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    return "\n\n".join([f"--- Fragment ---\n{r['content']}" for r in results])
                return f"Memory Error: {resp.status_code}"
        except Exception as e:
            return f"Library Access Error: {e}"

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        if tool_name not in self._tools:
            # Check for dynamic wallet tools
            if tool_name.startswith("wallet_"):
                return await self._execute_wallet(tool_name, args)
            raise ValueError(f"Unknown tool: {tool_name}")
        
        handler = self._tools[tool_name]
        return await handler(args)

    async def _generate_spore(self, args: Dict[str, Any]) -> str:
        from sos.services.tools.spore import SporeGenerator
        agent_name = args.get("agent_name", "River")
        generator = SporeGenerator(agent_name=agent_name)
        return generator.generate_spore()

    async def _generate_ui_asset(self, args: Dict[str, Any]) -> Dict[str, Any]:
        from sos.services.tools.assets import get_asset_generator
        generator = get_asset_generator()
        return await generator.generate_ui_asset(
            prompt=args.get("prompt", "A glowing mycelium node"),
            asset_type=args.get("asset_type", "card")
        )

    async def _execute_wallet(self, tool_name: str, args: Dict[str, Any]) -> Any:
        import httpx
        ECONOMY_URL = "http://localhost:8002"
        async with httpx.AsyncClient() as client:
            if tool_name == "wallet_balance":
                user_id = args.get("user_id")
                resp = await client.get(f"{ECONOMY_URL}/balance/{user_id}")
                return resp.json()
            elif tool_name == "wallet_debit":
                resp = await client.post(f"{ECONOMY_URL}/debit", json=args)
                return resp.json()
            elif tool_name == "wallet_credit":
                resp = await client.post(f"{ECONOMY_URL}/credit", json=args)
                return resp.json()
        return "Unknown wallet tool"

    async def _web_search(self, args: Dict[str, Any]) -> str:
        query = args.get("query")
        if not query:
            return "Error: Missing query"
            
        log.info(f"Executing web_search: {query}")
        try:
            from duckduckgo_search import DDGS
            results = DDGS().text(query, max_results=3)
            return str(results)
        except Exception as e:
            log.error(f"Web search failed: {e}")
            return f"Search Error: {e}"

    async def _filesystem_read(self, args: Dict[str, Any]) -> str:
        # Simple safe read for Phase 2
        path = args.get("path")
        if not path:
            return "Error: Missing path"

        # Security check: Must be within the workspace sandbox
        _sandbox = str(Path.home())
        if not path.startswith(_sandbox):
            return "Error: Access Denied (Sandbox violation)"

        try:
            with open(path, "r") as f:
                return f.read()
        except Exception as e:
            return f"Read Error: {e}"

    async def _filesystem_write(self, args: Dict[str, Any]) -> str:
        """Write content to a file (sandboxed to home directory)."""
        path = args.get("path")
        content = args.get("content")
        if not path:
            return "Error: Missing path"
        if content is None:
            return "Error: Missing content"

        # Security check: Must be within the workspace sandbox
        _sandbox = str(Path.home())
        if not path.startswith(_sandbox):
            return "Error: Access Denied (Sandbox violation)"

        # Block writing to sensitive paths
        blocked = [".ssh", ".env", ".bashrc", ".profile", "credentials", "secret"]
        if any(b in path.lower() for b in blocked):
            return "Error: Access Denied (Protected path)"

        try:
            with open(path, "w") as f:
                f.write(content)
            return f"Written {len(content)} bytes to {path}"
        except Exception as e:
            return f"Write Error: {e}"

    async def _bash_execute(self, args: Dict[str, Any]) -> str:
        """Execute a bash command (sandboxed, timeout enforced)."""
        import subprocess
        import shlex

        command = args.get("command")
        if not command:
            return "Error: Missing command"

        # Security: Block dangerous commands
        blocked_patterns = [
            "rm -rf /", "rm -rf /*", "mkfs", "dd if=", ":(){", "fork bomb",
            "> /dev/sd", "chmod -R 777 /", "wget | sh", "curl | sh",
            "sudo rm", "sudo mkfs", "sudo dd", ":(){ :|:& };:",
        ]
        cmd_lower = command.lower()
        for pattern in blocked_patterns:
            if pattern in cmd_lower:
                return f"Error: Blocked dangerous command pattern: {pattern}"

        # Security: Block sudo unless explicitly allowed
        if "sudo" in cmd_lower and not args.get("allow_sudo", False):
            return "Error: sudo requires explicit allow_sudo=True"

        # Set working directory to safe location (default to home)
        _sandbox = str(Path.home())
        cwd = args.get("cwd", _sandbox)
        if not cwd.startswith(_sandbox):
            cwd = _sandbox

        timeout = min(args.get("timeout", 30), 120)  # Max 2 minutes

        try:
            log.info(f"Executing bash: {command[:100]}...")
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
                env={**os.environ, "HOME": str(Path.home())}
            )

            output = result.stdout
            if result.stderr:
                output += f"\n[stderr]: {result.stderr}"
            if result.returncode != 0:
                output += f"\n[exit code: {result.returncode}]"

            # Truncate if too long
            if len(output) > 10000:
                output = output[:10000] + "\n... [truncated]"

            return output or "(no output)"
        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout}s"
        except Exception as e:
            return f"Bash Error: {e}"

    async def _google_drive_list(self, args: Dict[str, Any]) -> str:
        from sos.services.tools.google_auth import get_google_credentials
        from googleapiclient.discovery import build
        try:
            creds = get_google_credentials()
            service = build('drive', 'v3', credentials=creds)
            query = args.get("query", "mimeType='application/vnd.google-apps.spreadsheet' or mimeType='application/pdf'")
            results = service.files().list(
                pageSize=args.get("limit", 10), 
                q=query,
                fields="files(id, name, mimeType)").execute()
            items = results.get('files', [])
            return json.dumps(items, indent=2)
        except Exception as e:
            return f"Google Drive Error: {e}"

    async def _google_sheet_read(self, args: Dict[str, Any]) -> str:
        from sos.services.tools.google_auth import get_google_credentials
        from googleapiclient.discovery import build
        spreadsheet_id = args.get("spreadsheet_id")
        range_name = args.get("range", "Sheet1!A1:Z100")
        if not spreadsheet_id:
            return "Error: Missing spreadsheet_id"
        try:
            creds = get_google_credentials()
            service = build('sheets', 'v4', credentials=creds)
            sheet = service.spreadsheets()
            result = sheet.values().get(spreadsheet_id=spreadsheet_id, range=range_name).execute()
            values = result.get('values', [])
            return json.dumps(values, indent=2)
        except Exception as e:
            return f"Google Sheets Error: {e}"

from sos.services.tools.mcp_bridge import MCPBridge

class ToolsCore:
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self.local_tools = LocalTools()
        self.mcp_bridge = MCPBridge()
        self.plugins: Dict[str, PluginManifest] = {}
        self.plugin_tools: Dict[str, PluginManifest] = {}
        self.plugin_entrypoints: Dict[str, str] = {}
        self._load_plugins()

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        log.info(f"Tool Execution Request: {tool_name}")
        try:
            # Route to Local or MCP
            if "__" in tool_name:
                result = await self.mcp_bridge.execute(tool_name, args)
                self._audit_tool_call(tool_name, args, ok=True)
                return result

            if tool_name in self.plugin_tools:
                result = await self._execute_plugin_tool(tool_name, args)
                self._audit_tool_call(tool_name, args, ok=True)
                return result
                
            result = await self.local_tools.execute(tool_name, args)
            self._audit_tool_call(tool_name, args, ok=True)
            return result
        except Exception as exc:
            self._audit_tool_call(tool_name, args, ok=False, error=str(exc))
            raise

    async def list_tools(self) -> List[Dict[str, Any]]:
        local = [
            {"name": "bash", "description": "Execute bash commands (sandboxed to workspace, 2min timeout)"},
            {"name": "web_search", "description": "Search the web via DuckDuckGo"},
            {"name": "filesystem_read", "description": "Read a file (sandboxed to workspace)"},
            {"name": "filesystem_write", "description": "Write content to a file (sandboxed to workspace)"},
            {"name": "generate_spore", "description": "Generate a context-injection spore for agent state transfer"},
            {"name": "wallet_balance", "description": "Check wallet balance"},
            {"name": "wallet_debit", "description": "Debit funds from wallet"},
            {"name": "wallet_credit", "description": "Credit funds to wallet"},
            {"name": "google_drive_list", "description": "List files from Google Drive"},
            {"name": "google_sheet_read", "description": "Read values from a Google Sheet"},
            {"name": "query_library", "description": "Access the FRC library for deep physics reasoning"}
        ]
        plugins = [
            {
                "name": tool_name,
                "description": self.plugin_tools[tool_name].description or "Plugin tool",
                "plugin": self.plugin_tools[tool_name].name,
            }
            for tool_name in sorted(self.plugin_tools.keys())
        ]
        mcp = await self.mcp_bridge.list_tools()
        return local + plugins + mcp

    def _load_plugins(self) -> None:
        plugins_dir = self.config.paths.plugins_dir
        if not plugins_dir.exists():
            return

        for plugin_dir in plugins_dir.iterdir():
            if not plugin_dir.is_dir():
                continue
            manifest_path = plugin_dir / "plugin.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = json.loads(manifest_path.read_text())
                plugin = PluginManifest(
                    name=manifest["name"],
                    version=manifest["version"],
                    author=manifest.get("author"),
                    description=manifest.get("description"),
                    trust_level=manifest.get("trust_level", "community"),
                    capabilities_required=manifest.get("capabilities_required", []),
                    capabilities_provided=manifest.get("capabilities_provided", []),
                    entrypoints=manifest.get("entrypoints", {}),
                    sandbox=manifest.get("sandbox", {}),
                    signature=manifest.get("signature"),
                    path=plugin_dir,
                )
                if not self._trust_allowed(plugin.trust_level):
                    log.warning(f"Skipping plugin {plugin.name}: trust_level={plugin.trust_level}")
                    continue

                self.plugins[plugin.name] = plugin
                self._register_plugin_tools(plugin)
                log.info(f"Loaded plugin: {plugin.name}@{plugin.version}")
            except Exception as exc:
                log.error(f"Failed to load plugin at {manifest_path}: {exc}")

    def _register_plugin_tools(self, plugin: PluginManifest) -> None:
        entrypoints = plugin.entrypoints or {}
        tools_map = entrypoints.get("tools", {})
        if isinstance(entrypoints.get("tool"), str) and plugin.capabilities_provided:
            tool_name = plugin.capabilities_provided[0]
            tools_map.setdefault(tool_name, entrypoints["tool"])

        for tool_name in plugin.capabilities_provided:
            self.plugin_tools[tool_name] = plugin
            if tool_name in tools_map:
                self.plugin_entrypoints[tool_name] = tools_map[tool_name]

    def _trust_allowed(self, trust_level: str) -> bool:
        env = (os.getenv("SOS_ENV", "development") or "development").lower()
        if env == "production" and trust_level == "unsigned":
            return False
        return True

    async def _execute_plugin_tool(self, tool_name: str, args: Dict[str, Any]) -> Any:
        entrypoint = self.plugin_entrypoints.get(tool_name)
        if not entrypoint:
            raise ValueError(f"Plugin tool missing entrypoint: {tool_name}")

        module_path, func_name = entrypoint.split(":", 1)
        plugin = self.plugin_tools[tool_name]
        if plugin.path:
            sys.path.insert(0, str(plugin.path))
        try:
            module = importlib.import_module(module_path)
            handler = getattr(module, func_name)
            if inspect.iscoroutinefunction(handler):
                return await handler(args)
            return handler(args)
        finally:
            if plugin.path and str(plugin.path) in sys.path:
                sys.path.remove(str(plugin.path))

    def _audit_tool_call(self, tool_name: str, args: Dict[str, Any], ok: bool, error: Optional[str] = None) -> None:
        try:
            audit_dir = self.config.paths.data_dir / "audit"
            audit_dir.mkdir(parents=True, exist_ok=True)
            ledger_dir = self.config.paths.ledger_dir
            ledger_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool": tool_name,
                "ok": ok,
                "error": error,
                "args": args,
            }
            with open(audit_dir / "tools.jsonl", "a") as f:
                f.write(json.dumps(record) + "\n")
            with open(ledger_dir / "audit.jsonl", "a") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as exc:
            log.warning(f"Failed to write audit log: {exc}")
