#!/usr/bin/env python3
"""
Thin MCP stdio server wrapping Mirror's Sovereign Task System.
All tasks live in Mirror API (Supabase) — single source of truth.

Usage:
  claude mcp add tasks python3 /home/mumega/SOS/sos/mcp/tasks.py
"""
import sys
import json
import requests

MIRROR_URL = "http://localhost:8844"
MIRROR_TOKEN = "sk-mumega-internal-001"
HEADERS = {
    "Authorization": f"Bearer {MIRROR_TOKEN}",
    "Content-Type": "application/json",
}


def make_response(id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": id}
    if error:
        resp["error"] = {"code": -32000, "message": str(error)}
    else:
        resp["result"] = result
    return resp


def get_tools():
    return [
        {
            "name": "task_create",
            "description": "Create a sovereign task",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task title"},
                    "priority": {"type": "string", "enum": ["urgent", "high", "medium", "low"], "default": "medium"},
                    "project": {"type": "string", "description": "Project name"},
                    "description": {"type": "string", "description": "Task description"},
                    "agent": {"type": "string", "description": "Agent name", "default": "athena"},
                    "labels": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title"],
            },
        },
        {
            "name": "task_list",
            "description": "List tasks with optional filters",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "agent": {"type": "string", "description": "Filter by agent"},
                    "status": {"type": "string", "enum": ["backlog", "in_progress", "in_review", "done", "blocked", "canceled"]},
                    "project": {"type": "string", "description": "Filter by project"},
                },
            },
        },
        {
            "name": "task_update",
            "description": "Update a task's status or details",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID"},
                    "status": {"type": "string", "enum": ["backlog", "in_progress", "in_review", "done", "blocked", "canceled"]},
                    "title": {"type": "string"},
                    "priority": {"type": "string", "enum": ["urgent", "high", "medium", "low"]},
                    "description": {"type": "string"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "task_complete",
            "description": "Mark a task as done",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task ID"},
                },
                "required": ["task_id"],
            },
        },
        {
            "name": "task_stats",
            "description": "Get task statistics",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


def handle_tool_call(name, args):
    try:
        if name == "task_create":
            payload = {
                "title": args["title"],
                "priority": args.get("priority", "medium"),
                "agent": args.get("agent", "athena"),
            }
            if args.get("project"):
                payload["project"] = args["project"]
            if args.get("description"):
                payload["description"] = args["description"]
            if args.get("labels"):
                payload["labels"] = args["labels"]

            r = requests.post(f"{MIRROR_URL}/tasks", json=payload, headers=HEADERS, timeout=10)
            data = r.json()
            task_id = data.get("id", data.get("task_id", "?"))
            return {"content": [{"type": "text", "text": f"Created task: {task_id}"}]}

        elif name == "task_list":
            params = {}
            if args.get("agent"):
                params["agent"] = args["agent"]
            if args.get("status"):
                params["status"] = args["status"]
            if args.get("project"):
                params["project"] = args["project"]

            r = requests.get(f"{MIRROR_URL}/tasks", params=params, headers=HEADERS, timeout=10)
            data = r.json()
            tasks = data.get("tasks", data) if isinstance(data, dict) else data

            if isinstance(tasks, list) and tasks:
                summary = "\n".join(
                    f"- [{t.get('status', '?')}] {t.get('id', '?')}: {t.get('title', '?')} (P:{t.get('priority', '?')}, Agent:{t.get('agent', '?')})"
                    for t in tasks
                )
            else:
                summary = "No tasks found."
            return {"content": [{"type": "text", "text": summary}]}

        elif name == "task_update":
            task_id = args.pop("task_id")
            payload = {k: v for k, v in args.items() if v is not None}
            r = requests.put(f"{MIRROR_URL}/tasks/{task_id}", json=payload, headers=HEADERS, timeout=10)
            return {"content": [{"type": "text", "text": f"Updated task {task_id}"}]}

        elif name == "task_complete":
            task_id = args["task_id"]
            r = requests.post(f"{MIRROR_URL}/tasks/{task_id}/complete", headers=HEADERS, timeout=10)
            return {"content": [{"type": "text", "text": f"Completed task {task_id}"}]}

        elif name == "task_stats":
            r = requests.get(f"{MIRROR_URL}/tasks/stats", headers=HEADERS, timeout=10)
            stats = r.json()
            return {"content": [{"type": "text", "text": json.dumps(stats, indent=2, default=str)}]}

        else:
            return {"error": f"Unknown tool: {name}"}

    except requests.exceptions.ConnectionError:
        return {"content": [{"type": "text", "text": "Error: Mirror API not reachable at localhost:8844"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Error: {str(e)}"}]}


def main():
    """MCP stdio server main loop."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            resp = make_response(msg_id, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mumega-tasks", "version": "2.0.0"},
            })
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            resp = make_response(msg_id, {"tools": get_tools()})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            result = handle_tool_call(tool_name, tool_args)
            resp = make_response(msg_id, result)
        elif method == "ping":
            resp = make_response(msg_id, {})
        else:
            resp = make_response(msg_id, error=f"Unknown method: {method}")

        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
