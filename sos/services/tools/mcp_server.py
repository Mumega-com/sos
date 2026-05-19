import os
import json
import logging
import asyncio
import sys
# import docker # Removed for native mode
from typing import Dict, Any, List
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from sos.kernel import Config
from sos.observability.logging import get_logger

log = get_logger("tools_mcp")

app = FastAPI()

# Input Models
class WebSearchRequest(BaseModel):
    query: str
    count: int = 5
    provider: str = "auto"

class DeepResearchRequest(BaseModel):
    query: str
    count: int = 3
    depth: str = "standard" # standard, deep


# IMAGE_NAME = "sos-tools:latest" # Docker disabled for native mode

async def run_native_tool(script: str, args: List[str], env: Dict[str, str]) -> Dict[str, Any]:
    """
    Run a python script natively (via subprocess).
    WARNING: This runs without a sandbox. Ensure inputs are trusted.
    """
    try:
        # Resolve script path relative to this file's directory/docker
        # The scripts are still in the 'docker' folder, we just run them here.
        base_dir = os.path.dirname(os.path.abspath(__file__))
        script_path = os.path.join(base_dir, "docker", script)
        
        if not os.path.exists(script_path):
            return {"error": f"Tool script not found: {script}"}

        log.info(f"Executing native tool: {script}")
        
        # Merge current env with tool env (to get PATH, etc) but allow overrides
        full_env = os.environ.copy()
        full_env.update(env)
        
        # Ensure the script directory is in PYTHONPATH so tools can import each other
        script_dir = os.path.dirname(script_path)
        existing_pythonpath = full_env.get("PYTHONPATH", "")
        full_env["PYTHONPATH"] = f"{script_dir}:{existing_pythonpath}"

        proc = await asyncio.create_subprocess_exec(
            sys.executable, script_path, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=full_env
        )
        
        stdout, stderr = await proc.communicate()
        
        stdout_str = stdout.decode()
        stderr_str = stderr.decode()
        
        if stderr_str:
            log.warn(f"Tool {script} stderr: {stderr_str}")
            
        if proc.returncode != 0:
            log.error(f"Tool {script} failed with code {proc.returncode}")
            # Try to see if there's a JSON error in stdout
            try:
                error_obj = json.loads(stdout_str)
                if "error" in error_obj:
                    return error_obj
            except:
                pass
            return {"error": f"Tool execution failed: {stderr_str}"}
            
        log.debug(f"Tool {script} output: {stdout_str}")
        return json.loads(stdout_str)
        
    except Exception as e:
        log.error("Native execution error", error=str(e))
        return {"error": f"Execution Error: {str(e)}"}


@app.post("/tools/web_search")
async def web_search(req: WebSearchRequest):
    """
    Execute a web search (Native Mode).
    """
    # 1. Fetch Key from Kernel/Env
    tavily_key = os.getenv("TAVILY_API_KEY")
    
    env_vars = {}
    if tavily_key:
        env_vars["TAVILY_API_KEY"] = tavily_key
        
    # 2. Run Native
    result = await run_native_tool(
        script="web_search.py",
        args=[req.query, "--count", str(req.count), "--provider", req.provider],
        env=env_vars
    )
    
    return result

@app.post("/tools/deep_research")
async def deep_research(req: DeepResearchRequest):
    """
    Execute a deep research (Native Mode).
    """
    # 1. Fetch Key from Kernel/Env
    tavily_key = os.getenv("TAVILY_API_KEY")
    
    env_vars = {}
    if tavily_key:
        env_vars["TAVILY_API_KEY"] = tavily_key
        
    # 2. Run Native
    # Map depth to count if needed, or pass it directly if script supports it
    count = req.count
    if req.depth == "deep":
        count = max(count, 5)
        
    result = await run_native_tool(
        script="deep_research.py",
        args=[req.query, "--count", str(count)],
        env=env_vars
    )
    
    return result

class DocsRequest(BaseModel):
    action: str # write, read, list
    path: str = None
    content: str = None

@app.post("/tools/docs")
async def docs_tool(req: DocsRequest):
    """
    Manage Docusaurus Documentation.
    """
    args = ["--action", req.action]
    if req.path:
        args.extend(["--path", req.path])
    
    if req.content:
        args.extend(["--content", req.content])
        
    result = await run_native_tool(
        script="docs.py",
        args=args,
        env={}
    )
    return result

@app.get("/health")
def health():
    return {"status": "ok", "mode": "native"}

