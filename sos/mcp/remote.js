#!/usr/bin/env node
/**
 * SOS Remote MCP — connects any Claude Code to the Mumega swarm.
 * One file. Zero dependencies. Node.js only (ships with Claude Code).
 *
 * Env vars:
 *   SOS_URL    — Bus bridge URL (default: https://bus.mumega.com)
 *   SOS_TOKEN  — Auth token (required)
 *   MIRROR_URL — Mirror API URL (default: https://mumega.com/mirror)
 *   MIRROR_TOKEN — Mirror auth (default: same as SOS_TOKEN)
 *   AGENT      — Agent name (default: remote)
 *
 * Setup on Mac:
 *   claude mcp add --scope user \
 *     -e SOS_TOKEN=sk-bus-mumega-bridge-001 \
 *     -e MIRROR_TOKEN=sk-mumega-hadi-f084699a6a594313 \
 *     -e AGENT=hadi-mac \
 *     sos node ~/sos-remote.js
 *
 * Tools: send, inbox, peers, broadcast, remember, recall, memories
 */

const SOS_URL = (process.env.SOS_URL || "https://bus.mumega.com").replace(/\/$/, "");
const SOS_TOKEN = process.env.SOS_TOKEN || "";
const MIRROR_URL = (process.env.MIRROR_URL || "https://mumega.com/mirror").replace(/\/$/, "");
const MIRROR_TOKEN = process.env.MIRROR_TOKEN || SOS_TOKEN;
const AGENT = process.env.AGENT || "remote";

if (!SOS_TOKEN) {
  process.stderr.write("Error: SOS_TOKEN env var is required\n");
  process.exit(1);
}

// --- HTTP helpers (zero deps, native fetch) ---

async function bus(method, path, body) {
  const url = `${SOS_URL}${path}`;
  const opts = {
    method,
    headers: { "Authorization": `Bearer ${SOS_TOKEN}`, "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(url, opts);
    return await res.json();
  } catch (e) {
    return { error: e.message };
  }
}

async function mirror(method, path, body) {
  const url = `${MIRROR_URL}${path}`;
  const opts = {
    method,
    headers: { "Authorization": `Bearer ${MIRROR_TOKEN}`, "Content-Type": "application/json" },
  };
  if (body) opts.body = JSON.stringify(body);
  try {
    const res = await fetch(url, opts);
    return await res.json();
  } catch (e) {
    return { error: e.message };
  }
}

// --- Tools ---

const TOOLS = [
  { name: "ask", description: "Ask an agent directly and get a response (via OpenClaw, synchronous)",
    inputSchema: { type: "object", properties: {
      agent: { type: "string", description: "Agent name (e.g. athena, kasra)" },
      message: { type: "string", description: "Question or task" },
    }, required: ["agent", "message"] }},

  { name: "send", description: "Send async message to an agent on the server",
    inputSchema: { type: "object", properties: {
      to: { type: "string", description: "Agent name" },
      text: { type: "string", description: "Message" },
    }, required: ["to", "text"] }},

  { name: "inbox", description: "Check agent inbox",
    inputSchema: { type: "object", properties: {
      agent: { type: "string", default: AGENT },
      limit: { type: "number", default: 10 },
    }}},

  { name: "peers", description: "List all agents on the server",
    inputSchema: { type: "object", properties: {} }},

  { name: "broadcast", description: "Broadcast to all agents",
    inputSchema: { type: "object", properties: {
      text: { type: "string" },
    }, required: ["text"] }},

  { name: "remember", description: "Store a persistent memory",
    inputSchema: { type: "object", properties: {
      text: { type: "string", description: "Memory to store" },
      context: { type: "string", description: "Context label" },
    }, required: ["text"] }},

  { name: "recall", description: "Semantic search across memories",
    inputSchema: { type: "object", properties: {
      query: { type: "string" },
      limit: { type: "number", default: 5 },
    }, required: ["query"] }},

  { name: "memories", description: "List recent memories",
    inputSchema: { type: "object", properties: {
      limit: { type: "number", default: 10 },
    }}},
];

async function handle(name, args) {
  // --- Bus ---
  if (name === "ask") {
    const r = await bus("POST", "/ask", { agent: args.agent, message: args.message });
    if (r.error) return `Error: ${r.error}`;
    return r.reply || r.text || JSON.stringify(r);
  }

  if (name === "send") {
    const r = await bus("POST", "/send", { from: AGENT, to: args.to, text: args.text });
    return r.error ? `Error: ${r.error}` : `Sent to ${args.to}`;
  }

  if (name === "inbox") {
    const agent = args.agent || AGENT;
    const r = await bus("GET", `/inbox?agent=${agent}&limit=${args.limit || 10}`);
    if (r.error) return `Error: ${r.error}`;
    const msgs = r.messages || [];
    if (!msgs.length) return `No messages for ${agent}.`;
    return msgs.map(m => `[${m.timestamp || "?"}] ${m.source || "?"}: ${m.text || ""}`).join("\n");
  }

  if (name === "peers") {
    const r = await bus("GET", "/peers");
    if (r.error) return `Error: ${r.error}`;
    const reg = (r.registered || []).map(p => `${p.name || "?"} (${p.tool || "?"}) — ${p.summary || ""}`);
    const str = (r.streams || []).map(s => `${s.agent}: ${s.messages} msgs`);
    return `Live:\n${reg.join("\n") || "  none"}\n\nStreams:\n${str.join("\n") || "  none"}`;
  }

  if (name === "broadcast") {
    const r = await bus("POST", "/broadcast", { from: AGENT, text: args.text });
    return r.error ? `Error: ${r.error}` : `Broadcast sent`;
  }

  // --- Mirror ---
  if (name === "remember") {
    const ctx = args.context || `mcp-${Date.now()}`;
    const r = await mirror("POST", "/store", { text: args.text, agent: AGENT, context_id: ctx });
    return r.error ? `Error: ${r.error}` : `Stored: ${ctx}`;
  }

  if (name === "recall") {
    const r = await mirror("POST", "/search", { query: args.query, top_k: args.limit || 5, agent_filter: AGENT });
    if (r.error) return `Error: ${r.error}`;
    const results = Array.isArray(r) ? r : [];
    if (!results.length) return "No matching memories.";
    return results.map((e, i) =>
      `${i + 1}. [${(e.timestamp || "").slice(0, 10)}] ${e.raw_data?.text || e.context_id || "?"}`
    ).join("\n");
  }

  if (name === "memories") {
    const r = await mirror("GET", `/recent/${AGENT}?limit=${args.limit || 10}`);
    if (r.error) return `Error: ${r.error}`;
    const engrams = r.engrams || [];
    if (!engrams.length) return "No memories yet.";
    return engrams.map((e, i) =>
      `${i + 1}. [${(e.timestamp || "").slice(0, 10)}] ${e.raw_data?.text || e.context_id || "?"}`
    ).join("\n");
  }

  return `Unknown tool: ${name}`;
}

// --- MCP stdio server ---

const readline = require("readline");
const rl = readline.createInterface({ input: process.stdin, terminal: false });

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

// Auto-announce on startup
bus("POST", "/announce", { agent: AGENT, tool: "claude-code-remote", summary: "Remote Claude Code session" }).catch(() => {});

rl.on("line", async (line) => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }

  const { id, method, params } = msg;

  if (method === "initialize") {
    send({ jsonrpc: "2.0", id, result: {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "sos-remote", version: "2.0.0" },
    }});
  } else if (method === "tools/list") {
    send({ jsonrpc: "2.0", id, result: { tools: TOOLS } });
  } else if (method === "tools/call") {
    try {
      const text = await handle(params.name, params.arguments || {});
      send({ jsonrpc: "2.0", id, result: { content: [{ type: "text", text }] } });
    } catch (e) {
      send({ jsonrpc: "2.0", id, error: { code: -32000, message: e.message } });
    }
  } else if (method === "notifications/initialized") {
    // no-op
  } else if (method === "ping") {
    send({ jsonrpc: "2.0", id, result: {} });
  } else {
    send({ jsonrpc: "2.0", id, error: { code: -32601, message: "Unknown method" } });
  }
});

process.stderr.write(`SOS Remote MCP ready — agent=${AGENT} bus=${SOS_URL} mirror=${MIRROR_URL}\n`);
