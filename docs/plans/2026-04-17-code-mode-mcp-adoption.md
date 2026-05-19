# Code Mode MCP adoption — 99.9% token reduction for SOS tools

**Date:** 2026-04-17
**Author:** sos-dev
**Status:** Proposed (pending CF credentials)
**Depends on:** v0.4.0 Contracts (OpenAPI specs for SOS services) + v0.4.3 Dispatcher (CF Worker lives at the edge anyway)

## Why this matters

Cloudflare launched **Code Mode MCP Server** on 2026-04-13 as part of Agents Week.

Key number: **99.9% token reduction** for interacting with a tool catalog of 2,500 API endpoints — from 1.17 million tokens to roughly 1,000 tokens per agent session.

For SOS specifically:
- Every `mcp__sos__*` tool call today loads the full tool schema + agent's context + system prompt into the LLM's context window
- At ~100 tool calls per active session, that's tens of thousands of tokens burned on schema alone before the agent does a single useful thing
- SOS's stated token-economy constraint (Hadi: "Claude Code charges us when we directly send messages" + reference memory "Claude Code Max 20x" shows $164/5hr, $1949/week hard caps) means this compounds into real budget

Code Mode inverts the pattern: **agent writes code that calls tools, tools are progressively disclosed**, instead of pre-loading every schema upfront.

## What Code Mode does (inferred from CF announcement + public SDK)

Traditional MCP flow:
```
1. Agent connects to MCP server
2. Server sends full tools list (schemas, params, descriptions)
3. Agent's context = system prompt + tool schemas + user prompt
4. Agent generates tool call with JSON args
5. Server executes, returns result
6. Agent processes result in context
```
Problem: step 2 is O(N tools), even if the agent only needs one.

Code Mode flow:
```
1. Agent connects to MCP server
2. Server sends a tiny surface: "write code that uses our SDK; we'll execute it"
3. Agent writes code against progressively disclosed tool types
4. Server executes the code (sandbox), returns output
5. Agent processes output
```
Schema cost is amortized via type-inference rather than full pre-loading. Savings come from:
- Only schemas the agent actually uses get loaded
- Code output is terser than JSON tool-call-response pairs
- Repeat tool calls in the same session reuse loaded schemas

**Cost model shifts from per-tool-call to per-unique-tool-used.** An agent that calls `send` 100 times loads the `send` schema once, not 100 times.

## What SOS needs to adopt it

### Server-side changes (in SOS)

Minimal. SOS's MCP server (`sos/mcp/sos_mcp_sse.py`) already implements tools as functions. Code Mode adds a wrapper that:
1. Serves an SDK stub: `SOSClient` with typed methods for each tool
2. Executes agent-submitted code in a sandbox against that SDK
3. Returns sandbox stdout + any tool call results

The SDK is generated from OpenAPI specs, which v0.4.0 Contracts delivers for every service. **Symbiotic:** v0.4.0 makes Code Mode generation mechanical.

### Client-side changes (in agents)

**Almost none.** Claude Code, Codex CLI, Cursor, claude.ai — they all speak MCP. Code Mode is a new MCP *tool* pattern, not a new protocol. The client sees one tool (`execute_code`) that takes a code string, instead of 15 individual tools. Code generation is natural for Claude.

For Sonnet 4.6 and Opus 4.7 (our squad models), code generation is strength territory. Haiku might stumble on complex tool orchestration, but Haiku's role is pattern-replication (schema → Pydantic model), not multi-tool orchestration — so the effect on our squad is neutral-to-positive.

### Deployment shape

Where does the Code Mode server live?

**Option A: Inside dispatcher (v0.4.3 CF Worker)**
- Wrapper lives in `workers/sos-dispatcher/` as a Code Mode module
- Uses CF's Sandbox primitive (also announced Agents Week) for code execution
- Zero VPS changes
- Ships with dispatcher

**Option B: Separate CF Worker next to dispatcher**
- Dedicated `workers/sos-code-mode/` Worker
- Cleaner separation (dispatcher = auth + routing; code-mode = tool execution)
- Deploys independently

**Option C: On the VPS next to `:6070`**
- Python wrapper around existing MCP server
- Uses Python `exec()` or subprocess for code execution (riskier)
- Doesn't need CF at all
- Works in CF-agnostic deployment mode

**Recommendation:** Option A for v0.4.3, refactor to Option B in v0.5 if the dispatcher grows too big. Option C as fallback for bare-metal OSS deployments.

## Migration path

### Phase 1 — Shadow (v0.4.3 ships)
- Code Mode endpoint available as `mcp__sos__execute_code` alongside individual `mcp__sos__send`, `mcp__sos__inbox`, etc.
- Agents can opt in. Default stays individual tools.
- Measure token savings in the accounting ledger for opt-in sessions.

### Phase 2 — Default for new agents (v0.5)
- `sos-medic`, any new v0.5+ agent defaults to Code Mode
- Existing agents stay on individual-tool pattern (no forced migration)
- Measure cumulative token savings across new agents

### Phase 3 — Default for all (v0.6)
- Every agent's `CLAUDE.md` updated to recommend Code Mode
- Individual tools still work (back-compat) but decision log biases toward code-mode
- By this point, real data on savings vs. quality

### Phase 4 — Individual tools deprecated (v0.7 or later)
- Only if Phase 2+3 data shows clear wins with no regression
- Individual tools marked deprecated, remain for 2 minor versions
- After that, removed

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| Sandbox escape (agent writes code that escapes execution) | Use CF Sandbox (isolated V8) not local Python `exec`. CF Sandbox is designed for untrusted code. |
| Agent writes slow/infinite-loop code | Sandbox has CPU + wall-time limits |
| Agent writes code that makes external HTTP calls to exfiltrate data | Outbound Worker for Sandboxes (Apr 15) gates egress to allowlisted hosts |
| Code-gen fails for specific tool patterns | Keep individual-tool path as fallback; agent can retry with direct call |
| Haiku subagents struggle with code-gen | Their work shape (one schema → one file) fits a tiny wrapper function; keep individual tools available for them |
| Cost shifts to sandbox execution (CF Sandbox billing) | Measure. If cost > individual-tool savings, it's a loss; rare given CF Sandbox pricing vs. LLM token pricing (sandbox is $cents per session, tokens are $dollars per session) |

## Deliverables (when we build v0.4.3)

1. `workers/sos-dispatcher/src/code-mode.ts` — CF Worker module that serves the SDK stub + executes code in Sandbox
2. `scripts/generate-code-mode-sdk.py` — reads OpenAPI specs from `sos/contracts/openapi/` and generates TypeScript SDK stub
3. Updated MCP tool list: `mcp__sos__execute_code` joins the existing `send`, `inbox`, etc.
4. Contract tests: Code Mode execution produces same results as direct tool calls for a representative sample
5. Accounting hook instrumentation: tag each call as `direct` or `code-mode` in `~/.sos/accounting/<date>.jsonl`

## What does NOT change

- MCP protocol itself (we're using a new tool pattern, not a new protocol)
- SOS's internal tool implementations (they stay where they are)
- Client configurations (Claude Code's `.mcp.json` is unchanged; tool list grows by one)
- The dispatcher's auth/routing logic (Code Mode runs downstream of auth)

## Decisions open

| # | Question | Default |
|---|---|---|
| CM1 | Adopt Code Mode as part of v0.4.3? | Yes (biggest single token-economy win available) |
| CM2 | Deployment shape — Option A (inside dispatcher), B (separate Worker), or C (VPS Python)? | A for v0.4.3; B for v0.5; C as fallback for OSS forks |
| CM3 | Phase out individual tools by when? | v0.7 earliest, data-driven — don't pre-commit |
| CM4 | Include in the CF-agnostic deployment mode (v1.0)? | Optional. Individual tools always available; Code Mode is the CF-native variant. |

## Sources

- [Cloudflare Launches Code Mode MCP Server — InfoQ](https://www.infoq.com/news/2026/04/cloudflare-code-mode-mcp-server/)
- [Agents Week 2026 Updates](https://www.cloudflare.com/agents-week/updates/)
- [Enterprise MCP reference architecture](https://blog.cloudflare.com/enterprise-mcp/)
- Code Mode SDK — `github.com/cloudflare/agents` (search for code-mode subdirectory when repo is examined)

## One-line summary

Code Mode MCP is a 99.9% token reduction available as a managed CF service + open-source SDK. SOS adopts it as part of v0.4.3 dispatcher (Option A), shadow-tests in Phase 1, defaults new agents in v0.5, measures rigorously before expanding. Biggest single token-economy win available and it doesn't require changing any SOS core code.
