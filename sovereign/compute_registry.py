#!/usr/bin/env python3
"""
Sovereign Compute Registry — Every token source, sorted and documented.

This is the stomach of the organism. Every model the system can eat from,
organized by cost tier, with rate limits, capabilities, and access method.

Usage:
  python3 compute_registry.py           # show full inventory
  python3 compute_registry.py diesel    # show only free tier
  python3 compute_registry.py summary   # show totals
"""

import sys
import json

# ============================================
# THE COMPLETE COMPUTE INVENTORY
# ============================================

COMPUTE_SOURCES = {

    # ═══════════════════════════════════════════
    # TIER 0: SUBSCRIPTIONS (unlimited, flat rate)
    # ═══════════════════════════════════════════

    "claude_code_max": {
        "tier": "subscription",
        "provider": "Anthropic (Claude Code Max)",
        "monthly_cost": 120.00,
        "currency": "USD",
        "models": [
            {"id": "claude-opus-4-6", "context": "1M", "strength": "best", "use": "architecture, judgement, complex code"},
            {"id": "claude-sonnet-4-6", "context": "200K", "strength": "great", "use": "fast code, daily tasks"},
            {"id": "claude-haiku-4-5", "context": "200K", "strength": "good", "use": "bulk tasks, content"},
        ],
        "rate_limit": "~60 rpm (estimated)",
        "access": "Claude Code CLI / OpenClaw harness",
        "notes": "Primary compute. Unlimited within rate limits. All 3 core agents run here.",
    },

    "codex_cli": {
        "tier": "subscription",
        "provider": "OpenAI (Codex CLI via OpenClaw)",
        "monthly_cost": 0.00,
        "currency": "USD",
        "models": [
            {"id": "gpt-5.4", "context": "256K", "strength": "best", "use": "architecture, PM, planning"},
            {"id": "gpt-5.1", "context": "256K", "strength": "great", "use": "project agents (prefrontal, gaf)"},
            {"id": "gpt-4o", "context": "128K", "strength": "great", "use": "general tasks"},
        ],
        "rate_limit": "~30 rpm (estimated)",
        "access": "OpenClaw harness",
        "notes": "Included with GitHub Copilot. Athena runs on GPT-5.4.",
    },

    "gemini_cli": {
        "tier": "subscription",
        "provider": "Google (Gemini CLI via OpenClaw)",
        "monthly_cost": 0.00,
        "currency": "USD",
        "models": [
            {"id": "gemini-3.1-pro-preview", "context": "1M+", "strength": "great", "use": "River's brain, long context"},
        ],
        "rate_limit": "unknown",
        "access": "OpenClaw harness (google-gemini-cli)",
        "notes": "River runs here. Massive context window.",
    },

    # ═══════════════════════════════════════════
    # TIER 1: FREE API (daily limited, zero cost)
    # ═══════════════════════════════════════════

    "gemma4_ai_studio": {
        "tier": "free",
        "provider": "Google AI Studio",
        "monthly_cost": 0.00,
        "models": [
            {"id": "gemma-4-31b-it", "context": "256K", "strength": "excellent", "use": "content gen, reasoning, Sol's brain"},
            {"id": "gemma-4-26b-a4b-it", "context": "256K", "strength": "excellent", "use": "complex analysis (MoE)"},
        ],
        "rate_limit": "1,500 req/day",
        "access": "Google genai SDK (API key: GEMINI_API_KEY)",
        "notes": "#3 globally on LMArena. Sol's primary model. Free tier = 45,000 req/month.",
    },

    "gemini_ai_studio": {
        "tier": "free",
        "provider": "Google AI Studio",
        "monthly_cost": 0.00,
        "models": [
            {"id": "gemini-2.0-flash", "context": "1M", "strength": "good", "use": "fast tasks, fallback"},
            {"id": "gemini-2.5-flash", "context": "1M", "strength": "great", "use": "balanced speed+quality"},
            {"id": "gemini-3-flash-preview", "context": "1M", "strength": "great", "use": "latest flash"},
            {"id": "gemini-2.5-pro", "context": "1M", "strength": "excellent", "use": "complex reasoning"},
            {"id": "gemini-3-pro-preview", "context": "1M", "strength": "best", "use": "highest quality Gemini"},
            {"id": "gemma-3-27b-it", "context": "128K", "strength": "good", "use": "open model tasks"},
        ],
        "rate_limit": "1,500 req/day per model (free tier)",
        "access": "Google genai SDK",
        "notes": "Huge variety. Flash models for speed, Pro for quality. All free tier.",
    },

    "github_models": {
        "tier": "free",
        "provider": "GitHub Models (Azure)",
        "monthly_cost": 0.00,
        "models": [
            {"id": "gpt-4o-mini", "context": "128K", "strength": "good", "use": "quick generation, social posts"},
            {"id": "gpt-4o", "context": "128K", "strength": "great", "use": "complex tasks"},
            {"id": "Meta-Llama-3.1-405B-Instruct", "context": "128K", "strength": "great", "use": "open model, reasoning"},
            {"id": "Meta-Llama-3.1-8B-Instruct", "context": "128K", "strength": "fair", "use": "fast, cheap tasks"},
        ],
        "rate_limit": "~1,000 req/day",
        "access": "OpenAI SDK with base_url=models.inference.ai.azure.com, GITHUB_TOKEN",
        "notes": "Free with GitHub account. Includes embeddings (text-embedding-3-small/large).",
    },

    "copilot_haiku": {
        "tier": "free",
        "provider": "GitHub Copilot (Anthropic)",
        "monthly_cost": 0.00,
        "models": [
            {"id": "claude-haiku-4.5", "context": "200K", "strength": "good", "use": "blog, social, bulk content"},
        ],
        "rate_limit": "~5,000 req/day",
        "access": "OpenClaw harness (github-copilot/claude-haiku-4.5)",
        "notes": "Included with Copilot subscription. Blog + social agent use this.",
    },

    # ═══════════════════════════════════════════
    # TIER 2: CLOUDFLARE WORKERS AI ($5/mo plan)
    # ═══════════════════════════════════════════

    "cloudflare_workers_ai": {
        "tier": "cheap",
        "provider": "Cloudflare Workers AI",
        "monthly_cost": 5.00,
        "models": [
            {"id": "@cf/zai-org/glm-4.7-flash", "context": "131K", "strength": "great", "use": "agents, tool calling, multi-turn"},
            {"id": "@cf/meta/llama-4-scout-17b-instruct", "context": "128K", "strength": "good", "use": "general text gen"},
            {"id": "@cf/openai/gpt-oss-120b", "context": "128K", "strength": "great", "use": "large-scale text, code"},
            {"id": "@cf/google/embeddinggemma-300m", "context": "n/a", "strength": "embedding", "use": "RAG, semantic search"},
        ],
        "rate_limit": "~464,000 neurons/day (10K free + 454K paid)",
        "access": "Cloudflare Workers API / REST",
        "notes": "Edge compute. Low latency. Good for Cloudflare-deployed projects (Shabrang, TROP, Prefrontal).",
    },

    # ═══════════════════════════════════════════
    # TIER 3: METERED (pay per token, overflow)
    # ═══════════════════════════════════════════

    "xai_grok": {
        "tier": "metered",
        "provider": "xAI",
        "monthly_cost": "variable",
        "models": [
            {"id": "grok-4-1-fast-reasoning", "context": "2M", "strength": "great", "use": "long context, reasoning, cheap"},
        ],
        "rate_limit": "unlimited",
        "cost_per_1m": {"input": 0.20, "output": 0.50},
        "access": "OpenAI-compatible SDK at api.x.ai, XAI_API_KEY",
        "notes": "Cheapest paid frontier model. 2M context. Good for document analysis.",
    },

    "deepseek": {
        "tier": "metered",
        "provider": "DeepSeek",
        "monthly_cost": "variable",
        "models": [
            {"id": "deepseek-chat", "context": "64K", "strength": "great", "use": "reasoning, code review"},
            {"id": "deepseek-v3.2", "context": "64K", "strength": "great", "use": "reasoning with tool use"},
        ],
        "rate_limit": "unlimited",
        "cost_per_1m": {"input": 0.28, "output": 0.42},
        "access": "OpenAI-compatible SDK at api.deepseek.com, DEEPSEEK_API_KEY",
        "notes": "Excellent reasoning. Critique swarm uses deepseek-reasoner.",
    },

    "openai_metered": {
        "tier": "metered",
        "provider": "OpenAI (direct API)",
        "monthly_cost": "variable",
        "models": [
            {"id": "gpt-4o", "context": "128K", "strength": "great", "use": "complex tasks"},
            {"id": "gpt-4o-mini", "context": "128K", "strength": "good", "use": "fast, cheap"},
        ],
        "rate_limit": "unlimited",
        "cost_per_1m": {"input": 2.50, "output": 10.00},
        "access": "OpenAI SDK, OPENAI_API_KEY",
        "notes": "Only use as overflow when free sources exhausted.",
    },
}


# ═══════════════════════════════════════════
# FUEL GRADE MAPPING
# ═══════════════════════════════════════════

FUEL_GRADES = {
    "diesel": {
        "description": "Free/included — bulk work, content, social, routine",
        "sources": ["gemma4_ai_studio", "gemini_ai_studio", "github_models", "copilot_haiku", "cloudflare_workers_ai"],
        "cost": "$0/token",
    },
    "regular": {
        "description": "Cheap paid — support, code review, data processing",
        "sources": ["xai_grok", "deepseek"],
        "cost": "$0.20-0.50/1M tokens",
    },
    "premium": {
        "description": "Subscription — complex workflows, daily agent work",
        "sources": ["codex_cli", "gemini_cli"],
        "cost": "Flat rate (included)",
    },
    "aviation": {
        "description": "Top tier — architecture, judgement, critical decisions",
        "sources": ["claude_code_max"],
        "cost": "$120/mo flat",
    },
}


def show_inventory(tier_filter=None):
    """Display full compute inventory."""
    print(f"\n{'='*70}")
    print(f"  MUMEGA SOVEREIGN COMPUTE REGISTRY")
    print(f"{'='*70}")

    total_monthly = 0
    total_models = 0
    total_free_daily = 0

    for source_id, source in COMPUTE_SOURCES.items():
        if tier_filter and source["tier"] != tier_filter:
            continue

        tier_label = source["tier"].upper()
        cost = source["monthly_cost"]
        if isinstance(cost, (int, float)):
            total_monthly += cost

        models = source.get("models", [])
        total_models += len(models)

        rate = source.get("rate_limit", "unlimited")
        if "req/day" in str(rate):
            try:
                daily = int(str(rate).split(" ")[0].replace(",", "").replace("~", ""))
                total_free_daily += daily
            except:
                pass

        print(f"\n  [{tier_label}] {source['provider']}")
        if isinstance(cost, (int, float)) and cost > 0:
            print(f"    Cost: ${cost:.2f}/mo")
        elif source.get("cost_per_1m"):
            c = source["cost_per_1m"]
            print(f"    Cost: ${c['input']:.2f} in / ${c['output']:.2f} out per 1M tokens")
        else:
            print(f"    Cost: FREE")
        print(f"    Rate: {rate}")
        print(f"    Access: {source['access']}")

        for m in models:
            strength = m.get("strength", "?")
            ctx = m.get("context", "?")
            use = m.get("use", "")
            print(f"      • {m['id']:<40} {ctx:>6} ctx  [{strength}]  {use}")

    print(f"\n{'='*70}")
    print(f"  TOTALS")
    print(f"    Monthly fixed cost:  ${total_monthly:.2f}")
    print(f"    Total models:        {total_models}")
    print(f"    Free requests/day:   ~{total_free_daily:,}")
    print(f"    Free requests/month: ~{total_free_daily * 30:,}")
    print(f"{'='*70}")

    print(f"\n  FUEL GRADES")
    for grade, info in FUEL_GRADES.items():
        print(f"    {grade.upper():<12} {info['cost']:<25} {info['description']}")
    print()


def show_summary():
    """Quick summary."""
    subs = sum(s["monthly_cost"] for s in COMPUTE_SOURCES.values()
               if isinstance(s["monthly_cost"], (int, float)) and s["tier"] == "subscription")
    free = sum(s["monthly_cost"] for s in COMPUTE_SOURCES.values()
               if isinstance(s["monthly_cost"], (int, float)) and s["tier"] in ("free", "cheap"))
    models = sum(len(s.get("models", [])) for s in COMPUTE_SOURCES.values())

    print(f"\nCompute Summary:")
    print(f"  Subscriptions: ${subs:.0f}/mo (unlimited Claude + Codex + Gemini)")
    print(f"  Free/cheap:    ${free:.0f}/mo (AI Studio + GitHub + Cloudflare)")
    print(f"  Models:        {models} total across all providers")
    print(f"  Fuel grades:   diesel (free) → regular (cheap) → premium (sub) → aviation (top)")
    print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "summary":
            show_summary()
        elif arg in ("diesel", "free"):
            show_inventory("free")
        elif arg in ("subscription", "sub"):
            show_inventory("subscription")
        elif arg in ("metered", "paid"):
            show_inventory("metered")
        else:
            show_inventory(arg)
    else:
        show_inventory()
