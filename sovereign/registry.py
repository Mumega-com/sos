#!/usr/bin/env python3
"""
Sovereign Registry — Maps every thread of intelligence and every team.

Part 1: COMPUTE THREADS
  Every model session extracting tokens from providers.
  Where it runs, what it costs, how much quota remains.

Part 2: SQUADS
  Teams of agents + skills bundled for a function.
  Activate a squad on a project = instant capability.

Usage:
  python3 registry.py threads          # show all compute threads
  python3 registry.py squads           # show all squads
  python3 registry.py activate seo dnu # activate SEO squad on DNU
  python3 registry.py status           # full system map
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional

from kernel.config import MIRROR_URL, MIRROR_TOKEN, SOVEREIGN_DATA_DIR

REGISTRY_FILE = Path(SOVEREIGN_DATA_DIR) / "registry.json"
REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)

# ============================================
# PART 1: COMPUTE THREADS
# Every session extracting tokens from a provider
# ============================================

THREADS = {
    # === AVIATION (subscription, judgement) ===
    "kasra:opus": {
        "model": "claude-opus-4-6",
        "provider": "Anthropic",
        "source": "Claude Code Max ($120/mo)",
        "runtime": "tmux / openclaw",
        "context": "1M",
        "tier": "aviation",
        "cost": "subscription",
        "quota": "rate-limited (~60 rpm)",
        "best_for": ["architecture", "complex code", "debugging", "judgement calls"],
    },
    "athena:gpt54": {
        "model": "gpt-5.4",
        "provider": "OpenAI",
        "source": "Codex CLI (included)",
        "runtime": "openclaw",
        "context": "256K",
        "tier": "aviation",
        "cost": "subscription",
        "quota": "rate-limited (~30 rpm)",
        "best_for": ["PM", "planning", "decomposition", "strategy"],
    },
    "athena:gemini": {
        "model": "gemini-3.1-pro-preview",
        "provider": "Google",
        "source": "Gemini CLI (included)",
        "runtime": "openclaw",
        "context": "1M+",
        "tier": "aviation",
        "cost": "subscription",
        "quota": "unknown",
        "best_for": ["long context", "research", "document analysis"],
    },

    # === REGULAR (subscription, daily work) ===
    "kasra:sonnet": {
        "model": "claude-sonnet-4-6",
        "provider": "Anthropic",
        "source": "Claude Code Max ($120/mo)",
        "runtime": "openclaw",
        "context": "200K",
        "tier": "regular",
        "cost": "subscription",
        "quota": "rate-limited",
        "best_for": ["fast code", "reviews", "refactoring"],
    },
    "worker:haiku": {
        "model": "claude-haiku-4-5",
        "provider": "Anthropic",
        "source": "Copilot (included)",
        "runtime": "openclaw / script",
        "context": "200K",
        "tier": "regular",
        "cost": "free",
        "quota": "~5000/day",
        "best_for": ["blog", "social content", "bulk tasks"],
    },

    # === DIESEL (free, high volume) ===
    "worker:gemma4": {
        "model": "gemma-4-31b-it",
        "provider": "Google",
        "source": "AI Studio (free)",
        "runtime": "script (sovereign loop)",
        "context": "256K",
        "tier": "diesel",
        "cost": "free",
        "quota": "1500/day",
        "best_for": ["content gen", "reasoning", "scoring", "research"],
    },
    "worker:gemma26": {
        "model": "gemma-4-26b-a4b-it",
        "provider": "Google",
        "source": "AI Studio (free)",
        "runtime": "script",
        "context": "256K",
        "tier": "diesel",
        "cost": "free",
        "quota": "1500/day",
        "best_for": ["complex analysis", "MoE tasks"],
    },
    "worker:flash": {
        "model": "gemini-2.0-flash",
        "provider": "Google",
        "source": "AI Studio (free)",
        "runtime": "script",
        "context": "1M",
        "tier": "diesel",
        "cost": "free",
        "quota": "1500/day",
        "best_for": ["fast tasks", "fallback", "simple generation"],
    },
    "worker:github": {
        "model": "gpt-4o-mini",
        "provider": "GitHub/Azure",
        "source": "GitHub Models (free)",
        "runtime": "script",
        "context": "128K",
        "tier": "diesel",
        "cost": "free",
        "quota": "1000/day",
        "best_for": ["quick gen", "social posts", "drafts"],
    },
    "worker:openrouter": {
        "model": "openrouter/free (28 models)",
        "provider": "OpenRouter",
        "source": "OpenRouter (free tier)",
        "runtime": "script",
        "context": "varies",
        "tier": "diesel",
        "cost": "free",
        "quota": "200/day",
        "best_for": ["last resort fallback", "variety"],
    },
    "worker:cloudflare": {
        "model": "GLM-4.7-Flash / Llama-4-Scout",
        "provider": "Cloudflare",
        "source": "Workers AI ($5/mo)",
        "runtime": "edge (Cloudflare Workers)",
        "context": "131K",
        "tier": "diesel",
        "cost": "$5/mo",
        "quota": "464K neurons/day",
        "best_for": ["edge compute", "webhooks", "low latency"],
    },

    # === OVERFLOW (metered, pay per use) ===
    "worker:deepseek": {
        "model": "deepseek-chat / v3.2",
        "provider": "DeepSeek",
        "source": "DeepSeek API (metered)",
        "runtime": "script",
        "context": "64K",
        "tier": "overflow",
        "cost": "$0.28/1M tokens",
        "quota": "unlimited",
        "best_for": ["reasoning", "code review", "critique swarm"],
    },
    "worker:grok": {
        "model": "grok-4-1-fast-reasoning",
        "provider": "xAI",
        "source": "xAI API (metered)",
        "runtime": "script",
        "context": "2M",
        "tier": "overflow",
        "cost": "$0.20/1M tokens",
        "quota": "unlimited",
        "best_for": ["long docs", "2M context", "cheap paid"],
    },
}


# ============================================
# PART 2: SQUADS
# Teams bundled for a function, deployable on any project
# ============================================

SQUADS = {
    "seo": {
        "name": "SEO Squad",
        "description": "Search engine optimization — audit, keywords, content optimization, ranking",
        "roles": {
            "analyst": {
                "thread": "worker:gemma4",
                "skills": ["search_console_check", "keyword_research", "competitor_analysis"],
                "schedule": "daily 9am",
                "does": "Pull Search Console data, find keyword opportunities, track rankings",
            },
            "optimizer": {
                "thread": "worker:flash",
                "skills": ["meta_optimization", "internal_linking", "schema_markup"],
                "schedule": "weekly Monday",
                "does": "Audit pages, fix meta tags, suggest internal links",
            },
            "writer": {
                "thread": "worker:gemma4",
                "skills": ["seo_content", "blog_writing", "landing_page_copy"],
                "schedule": "3x/week",
                "does": "Write SEO-optimized blog posts targeting keyword gaps",
            },
            "reporter": {
                "thread": "worker:github",
                "skills": ["analytics_report", "ranking_report"],
                "schedule": "weekly Friday",
                "does": "Generate weekly SEO performance report for client",
            },
        },
        "kpis": ["organic_traffic", "keyword_rankings", "pages_indexed", "backlinks"],
        "tools_needed": ["google_search_console", "google_analytics", "sitepilot"],
    },
    "content": {
        "name": "Content Squad",
        "description": "Content creation — blog, social, email, video scripts",
        "roles": {
            "strategist": {
                "thread": "athena:gpt54",
                "skills": ["content_calendar", "topic_research", "audience_analysis"],
                "schedule": "weekly Monday",
                "does": "Plan content calendar, research topics, define audience",
            },
            "writer": {
                "thread": "worker:gemma4",
                "skills": ["blog_writing", "long_form", "copywriting"],
                "schedule": "daily",
                "does": "Write blog posts, articles, landing page copy",
            },
            "social": {
                "thread": "worker:github",
                "skills": ["social_posting", "caption_writing", "hashtag_research"],
                "schedule": "4x/day",
                "does": "Create and post social media content across platforms",
            },
            "email": {
                "thread": "worker:flash",
                "skills": ["email_sequences", "newsletter", "drip_campaigns"],
                "schedule": "2x/week",
                "does": "Draft email campaigns, nurture sequences",
            },
        },
        "kpis": ["posts_published", "engagement_rate", "email_open_rate", "subscriber_growth"],
        "tools_needed": ["wordpress", "social_accounts", "email_platform"],
    },
    "leadgen": {
        "name": "Lead Generation Squad",
        "description": "Find, qualify, and nurture leads",
        "roles": {
            "scanner": {
                "thread": "worker:gemma4",
                "skills": ["google_maps_scan", "directory_scraping", "lead_enrichment"],
                "schedule": "daily",
                "does": "Scan Google Maps, directories for potential leads",
            },
            "qualifier": {
                "thread": "worker:flash",
                "skills": ["lead_scoring", "business_research", "contact_finding"],
                "schedule": "daily",
                "does": "Score and qualify raw leads, find decision-maker contacts",
            },
            "outreach": {
                "thread": "worker:gemma4",
                "skills": ["email_drafting", "follow_up_sequences", "personalization"],
                "schedule": "daily",
                "does": "Draft personalized outreach emails, manage follow-up cadence",
            },
            "crm": {
                "thread": "worker:github",
                "skills": ["ghl_management", "pipeline_tracking", "contact_updates"],
                "schedule": "continuous",
                "does": "Push leads to GHL, update pipeline stages, track conversions",
            },
        },
        "kpis": ["leads_found", "leads_qualified", "outreach_sent", "meetings_booked", "conversion_rate"],
        "tools_needed": ["google_maps_api", "ghl", "email"],
    },
    "analytics": {
        "name": "Analytics Squad",
        "description": "Data monitoring, reporting, anomaly detection",
        "roles": {
            "monitor": {
                "thread": "worker:flash",
                "skills": ["ga4_check", "uptime_monitoring", "anomaly_detection"],
                "schedule": "every 6h",
                "does": "Check GA4, detect traffic anomalies, monitor uptime",
            },
            "reporter": {
                "thread": "worker:gemma4",
                "skills": ["weekly_report", "client_dashboard", "insight_generation"],
                "schedule": "weekly Friday",
                "does": "Generate client-facing analytics reports with insights",
            },
        },
        "kpis": ["report_accuracy", "anomalies_detected", "client_satisfaction"],
        "tools_needed": ["google_analytics", "google_search_console"],
    },
    "wordpress": {
        "name": "WordPress Squad",
        "description": "Site management, content publishing, maintenance",
        "roles": {
            "publisher": {
                "thread": "worker:flash",
                "skills": ["wp_publishing", "media_upload", "category_management"],
                "schedule": "on-demand",
                "does": "Publish content to WordPress, manage media library",
            },
            "maintainer": {
                "thread": "worker:cloudflare",
                "skills": ["plugin_updates", "security_scan", "performance_check"],
                "schedule": "weekly",
                "does": "Check plugin updates, run security scan, monitor performance",
            },
            "seo_tech": {
                "thread": "worker:flash",
                "skills": ["rank_math", "sitemap", "robots_txt", "schema"],
                "schedule": "weekly",
                "does": "Manage Rank Math, sitemaps, structured data",
            },
        },
        "kpis": ["uptime", "page_speed", "security_score", "content_freshness"],
        "tools_needed": ["sitepilot", "wp_cli"],
    },
    "outbound": {
        "name": "Outbound Sales Squad",
        "description": "Cold outreach, follow-ups, meeting booking",
        "roles": {
            "researcher": {
                "thread": "worker:gemma4",
                "skills": ["prospect_research", "company_analysis", "pain_point_mapping"],
                "schedule": "daily",
                "does": "Research prospects, map their pain points, find triggers",
            },
            "writer": {
                "thread": "worker:gemma4",
                "skills": ["cold_email", "linkedin_message", "proposal_draft"],
                "schedule": "daily",
                "does": "Write personalized cold emails and LinkedIn messages",
            },
            "followup": {
                "thread": "worker:github",
                "skills": ["follow_up_cadence", "reminder_scheduling"],
                "schedule": "daily",
                "does": "Send follow-ups at right intervals, manage cadence",
            },
        },
        "kpis": ["emails_sent", "reply_rate", "meetings_booked", "pipeline_value"],
        "tools_needed": ["email", "linkedin", "ghl"],
    },
}


# ============================================
# ACTIVATION — Deploy a squad on a project
# ============================================

def activate_squad(squad_name: str, project: str):
    """Activate a squad on a project. Creates tasks in Mirror for each role."""
    import requests

    if squad_name not in SQUADS:
        print(f"Unknown squad: {squad_name}")
        print(f"Available: {', '.join(SQUADS.keys())}")
        return

    squad = SQUADS[squad_name]
    print(f"\nActivating {squad['name']} on {project}...")
    print(f"Description: {squad['description']}")
    print(f"KPIs: {', '.join(squad['kpis'])}")
    print(f"Tools needed: {', '.join(squad['tools_needed'])}")
    print()

    HEADERS = {"Authorization": f"Bearer {MIRROR_TOKEN}", "Content-Type": "application/json"}

    for role_name, role in squad["roles"].items():
        task_title = f"[{squad_name.upper()}] {role_name}: {role['does'][:60]}"
        print(f"  Creating: {task_title}")
        try:
            requests.post(f"{MIRROR_URL}/tasks", json={
                "title": task_title,
                "agent": role["thread"].split(":")[0],
                "priority": "medium",
                "project": project,
                "description": f"Squad: {squad['name']}\nRole: {role_name}\nThread: {role['thread']}\nSchedule: {role['schedule']}\nDoes: {role['does']}\nSkills: {', '.join(role['skills'])}",
                "labels": ["squad", squad_name, role_name],
            }, headers=HEADERS, timeout=10)
        except:
            print(f"    ⚠️ Failed to create task")

    print(f"\n✅ {squad['name']} activated on {project}")
    print(f"   {len(squad['roles'])} roles created as Mirror tasks")
    print(f"   Run: python3 ~/sovereign/registry.py status")


# ============================================
# DISPLAY
# ============================================

def show_threads():
    print(f"\n{'='*70}")
    print(f"  COMPUTE THREADS — Every source of intelligence")
    print(f"{'='*70}")

    for tier in ["aviation", "regular", "diesel", "overflow"]:
        tier_threads = {k: v for k, v in THREADS.items() if v["tier"] == tier}
        if not tier_threads:
            continue
        print(f"\n  [{tier.upper()}]")
        for thread_id, t in tier_threads.items():
            quota = t.get("quota", "?")
            print(f"    {thread_id:<22} {t['model']:<30} {t['source']:<25} {quota}")

    total = len(THREADS)
    free = sum(1 for t in THREADS.values() if t["cost"] == "free")
    print(f"\n  Total: {total} threads | Free: {free} | Paid: {total - free}")


def show_squads():
    print(f"\n{'='*70}")
    print(f"  SQUADS — Deployable teams")
    print(f"{'='*70}")

    for squad_id, squad in SQUADS.items():
        roles = len(squad["roles"])
        threads = set(r["thread"] for r in squad["roles"].values())
        cost_threads = [THREADS.get(t, {}).get("cost", "?") for t in threads]
        all_free = all(c in ("free", "subscription") for c in cost_threads)
        cost_label = "FREE" if all_free else "PAID"

        print(f"\n  [{squad_id.upper()}] {squad['name']} ({roles} roles, {cost_label})")
        print(f"    {squad['description']}")
        for role_name, role in squad["roles"].items():
            print(f"      • {role_name:<12} → {role['thread']:<20} {role['schedule']:<15} {role['does'][:40]}")
        print(f"    KPIs: {', '.join(squad['kpis'])}")
        print(f"    Needs: {', '.join(squad['tools_needed'])}")


def show_status():
    show_threads()
    show_squads()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"

    if cmd == "threads":
        show_threads()
    elif cmd == "squads":
        show_squads()
    elif cmd == "activate":
        if len(sys.argv) >= 4:
            activate_squad(sys.argv[2], sys.argv[3])
        else:
            print("Usage: registry.py activate <squad> <project>")
            print(f"Squads: {', '.join(SQUADS.keys())}")
    elif cmd == "status":
        show_status()
    else:
        show_status()
