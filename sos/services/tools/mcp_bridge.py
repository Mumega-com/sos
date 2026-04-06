import importlib
import json
import os
import sys
from typing import Any, Dict, List, Optional
from pathlib import Path

from sos.kernel.config import Config
from sos.observability.logging import get_logger

log = get_logger("mcp_bridge")

class MCPBridge:
    """
    Bridges SOS to optional MCP-style server classes.

    Server loading is explicit. SOS no longer hardwires the legacy CLI path into
    sys.path; if legacy servers are still needed, point to them intentionally
    via config or environment.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self.servers = {}
        self._discover_local_servers()

    def _load_server_specs(self) -> List[Dict[str, Any]]:
        """
        Load configured MCP server class specs.

        Supported sources:
        - SOS_MCP_SERVER_MODULES env var containing JSON
        - config.services["mcp"]["servers"] from ~/.sos/config/sos.json

        Spec format:
        {
          "module": "pkg.module",
          "class": "ServerClass",
          "name": "optional-explicit-name",
          "config": {"optional": "server init config"},
          "sys_path": ["/optional/import/root"]
        }
        """
        env_specs = os.environ.get("SOS_MCP_SERVER_MODULES", "").strip()
        if env_specs:
            try:
                parsed = json.loads(env_specs)
                if isinstance(parsed, list):
                    return parsed
                log.warn("SOS_MCP_SERVER_MODULES must be a JSON list")
            except Exception as e:
                log.error("Failed to parse SOS_MCP_SERVER_MODULES", error=str(e))

        mcp_cfg = self.config.get_service_config("mcp")
        specs = mcp_cfg.get("servers", [])
        return specs if isinstance(specs, list) else []

    def _discover_local_servers(self):
        """
        Load explicitly configured server classes.
        """
        try:
            server_specs = self._load_server_specs()
            if not server_specs:
                log.info("No MCP server specs configured; skipping bridge discovery")
                return

            for spec in server_specs:
                try:
                    module_path = spec["module"]
                    class_name = spec["class"]
                    init_config = spec.get("config", {})
                    extra_paths = spec.get("sys_path", [])

                    for raw_path in extra_paths:
                        path = str(Path(raw_path).resolve())
                        if path not in sys.path:
                            sys.path.append(path)

                    mod = importlib.import_module(module_path)
                    cls = getattr(mod, class_name)
                    server = cls(init_config)
                    server_name = spec.get("name") or server.get_server_name()
                    self.servers[server_name] = server
                    log.info(f"Loaded MCP Server: {server_name}")
                except KeyError as e:
                    log.warn(f"Skipping malformed MCP server spec missing key: {e}")
                except ImportError as e:
                    log.warn(f"Could not load {module_path}: {e}")
                except Exception as e:
                    log.error(f"Failed to init {class_name}: {e}")

        except Exception as e:
            log.error("MCP Discovery Failed", error=str(e))

    async def list_tools(self) -> List[Dict[str, Any]]:
        tools = []
        for server_name, server in self.servers.items():
            for tool_name in server.get_available_tools():
                tools.append({
                    "name": f"{server_name}__{tool_name}",
                    "description": f"MCP Tool from {server_name}",
                    "source": "mcp"
                })
        return tools

    async def execute(self, tool_name: str, args: Dict[str, Any]) -> Any:
        if "__" not in tool_name:
            raise ValueError("Invalid MCP tool format. Expected server__tool")
        
        server_name, real_tool_name = tool_name.split("__", 1)
        
        if server_name not in self.servers:
            raise ValueError(f"Unknown MCP Server: {server_name}")
            
        server = self.servers[server_name]
        return await server.execute_tool(real_tool_name, args)
