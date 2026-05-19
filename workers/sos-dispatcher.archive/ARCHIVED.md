# ARCHIVED — Cloudflare Worker Dispatcher Scaffold

**Date archived:** 2026-04-17

## Why

Competitive scan conducted 2026-04-06 to 2026-04-16 revealed that OpenAI Agents SDK, Claude Managed Agents (Anthropic), Microsoft Agent Framework, and LangGraph all shipped production runtimes in the preceding six weeks. Building and maintaining a generic CF Worker dispatcher scaffold provides no differentiation against these well-resourced production runtimes.

Content is preserved in git history. The directory is renamed (not deleted) so new work does not accidentally extend it.

## What Was Learned (Kept)

The portable dispatcher **protocol document** at `docs/plans/2026-04-15-dispatcher-protocol.md` remains valid and is not archived. It defines the per-squad backend routing contract and is still applicable to the replacement orchestrator.

## Replacement

The differentiating work is the **Claude-Code-Squad orchestrator**, which handles:
- Wake daemon coordination (`sos/services/wake_daemon/`)
- Squad task orchestration (`sos/services/squad/`)
- Session-state persistence and tmux handoffs
- MCP-aware squad coordination
- Future: Provider Matrix at `sos/providers/`

This orchestrator is specific to the Mumega multi-agent organism model and is NOT commoditized by the generic runtimes above.

## If Future-You Wants to Unarchive

Re-check whether OpenAI/Anthropic/LangGraph commoditization still holds. As of 2026-04-17, all four frameworks have shipped production-grade generic dispatching. The bar to justify un-archiving is: a capability gap in all four frameworks that only a custom scaffold can fill.

It will still hold.
