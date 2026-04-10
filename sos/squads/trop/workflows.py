"""TROP Squad Workflows — automated content generation, distribution, health.

The Realm of Patterns digestive system:
  Content (transits, archetypes) goes in → Users come out → Revenue flows.

All workflows call EXISTING TROP endpoints. Nothing is rebuilt.
  - /api/daily-update → generates cosmic weather
  - /api/narrator → generates AI narratives
  - /api/share → distributes to social
  - /api/remind → schedules email
  - /api/publish → stores content

Run workflows via:
  python -m sos.squads.trop daily       # Daily cosmic content + distribute
  python -m sos.squads.trop weekly      # Weekly deep content
  python -m sos.squads.trop monthly     # Monthly tarot + I Ching
  python -m sos.squads.trop social      # Social media posting
  python -m sos.squads.trop health      # Health check
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger("sos.squads.trop")

# TROP endpoints (Cloudflare Pages Functions)
TROP_BASE = os.environ.get("TROP_URL", "https://therealmofpatterns.com")
TROP_ADMIN_KEY = os.environ.get("TROP_ADMIN_KEY", "")
MIRROR_URL = os.environ.get("MIRROR_URL", "http://localhost:8844")
MIRROR_TOKEN = os.environ.get("MIRROR_TOKEN", "sk-mumega-internal-001")

# Telegram channel for TROP (direct posting — primary distribution)
TROP_TELEGRAM_BOT_TOKEN = os.environ.get("TROP_TELEGRAM_BOT_TOKEN", "8682793155:AAGOiy0OGILgydZ7S_HrWB4e63gNOT4DC4U")
TROP_TELEGRAM_CHAT_ID = os.environ.get("TROP_TELEGRAM_CHAT_ID", "-1003713252924")


def _trop_request(path: str, method: str = "POST", body: dict | None = None) -> dict:
    """Make an authenticated request to TROP API."""
    url = f"{TROP_BASE}/api/{path}"
    headers = {"Content-Type": "application/json"}
    payload = body or {}
    if TROP_ADMIN_KEY:
        payload["admin_key"] = TROP_ADMIN_KEY

    try:
        resp = requests.request(method, url, json=payload, headers=headers, timeout=60)
        if resp.status_code == 200:
            return resp.json()
        logger.warning("TROP API %s returned %d: %s", path, resp.status_code, resp.text[:200])
        return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:200]}
    except Exception as exc:
        logger.error("TROP API %s failed: %s", path, exc)
        return {"error": str(exc)}


def _post_to_telegram(text: str, parse_mode: str = "Markdown") -> bool:
    """Post directly to TROP Telegram channel. Primary distribution."""
    if not TROP_TELEGRAM_BOT_TOKEN or not TROP_TELEGRAM_CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TROP_TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TROP_TELEGRAM_CHAT_ID, "text": text, "parse_mode": parse_mode},
            timeout=15,
        )
        if resp.status_code == 200:
            logger.info("Posted to TROP Telegram channel")
            return True
        logger.warning("Telegram post failed: %d %s", resp.status_code, resp.text[:100])
    except Exception as exc:
        logger.warning("Telegram post error: %s", exc)
    return False


def _store_in_mirror(text: str, context: str) -> None:
    """Store workflow result in Mirror for the organism's memory."""
    try:
        requests.post(
            f"{MIRROR_URL}/store",
            json={
                "text": text,
                "agent": "trop-squad",
                "context_id": context,
                "project": "therealmofpatterns",
                "core_concepts": ["trop", "content", "astrology"],
            },
            headers={"Authorization": f"Bearer {MIRROR_TOKEN}"},
            timeout=10,
        )
    except Exception:
        pass


# ── 1. DAILY COSMIC CONTENT ──────────────────────────────────────────────────

def daily_content() -> dict[str, Any]:
    """Generate daily cosmic weather + distribute.

    Two-tier approach:
    1. DETERMINISTIC: Real ephemeris → templates → instant content (always works)
    2. LLM ENRICHMENT: Call /api/daily-update for Gemini-enhanced version (best effort)

    Distributes: blog publish, social post, Mirror memory.
    """
    logger.info("TROP daily content workflow starting")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Step 1: Deterministic content from real ephemeris (always works, no API needed)
    from sos.squads.trop.ephemeris import get_today, generate_daily_reading, generate_social_post

    cosmic_data = get_today()
    reading = generate_daily_reading(cosmic_data)
    social_text = generate_social_post(cosmic_data)

    logger.info(
        "Ephemeris: Sun %s, Moon %s (%s), dominant %s",
        cosmic_data["sun_sign"], cosmic_data["moon_sign"],
        cosmic_data["moon_phase"]["name"], cosmic_data["dominant"]["name"],
    )

    # Step 2: Publish deterministic reading to TROP
    publish_result = _trop_request("publish", body={
        "content_type": "daily_weather",
        "language": "en",
        "title": f"Cosmic Weather — {today}",
        "body": reading,
        "params": {
            "date": today,
            "sun_sign": cosmic_data["sun_sign"],
            "moon_sign": cosmic_data["moon_sign"],
            "moon_phase": cosmic_data["moon_phase"]["name"],
            "dominant": cosmic_data["dominant"]["name"],
            "signature": cosmic_data["signature"],
            "vector": cosmic_data["vector"],
        },
    })

    # Step 3: Try LLM-enriched version (best effort, may fail)
    llm_result = _trop_request("daily-update", body={"date": today, "languages": ["en"]})
    llm_count = llm_result.get("content_generated", 0) if not llm_result.get("error") else 0

    # Step 4: Distribute — Telegram direct (primary), TROP API share (secondary)
    social_results = {}

    # Primary: Direct Telegram posting (always works)
    social_results["telegram_reading"] = _post_to_telegram(reading)
    social_results["telegram_social"] = _post_to_telegram(
        f"✨ {social_text}\n\n#astrology #cosmicweather #therealmofpatterns #jungian"
    )

    # Secondary: TROP share API (best effort, needs auth)
    twitter_result = _trop_request("share", body={
        "platform": "twitter",
        "text": f"{social_text}\n\n#astrology #cosmicweather #therealmofpatterns",
    })
    social_results["twitter"] = twitter_result

    # Step 5: Store in Mirror
    _store_in_mirror(
        f"TROP daily: {today}. {cosmic_data['sun_sign']} Sun, {cosmic_data['moon_sign']} Moon, "
        f"{cosmic_data['moon_phase']['name']}. Dominant: {cosmic_data['dominant']['name']}. "
        f"Deterministic: {len(reading)} chars. LLM: {llm_count} pieces.",
        f"trop-daily-{today}",
    )

    logger.info("Daily workflow complete: %d chars deterministic, %d LLM pieces", len(reading), llm_count)
    return {
        "date": today,
        "cosmic_data": {
            "sun": cosmic_data["sun_sign"],
            "moon": cosmic_data["moon_sign"],
            "phase": cosmic_data["moon_phase"]["name"],
            "dominant": cosmic_data["dominant"]["name"],
        },
        "reading_length": len(reading),
        "llm_pieces": llm_count,
        "published": publish_result,
        "social": social_results,
    }


# ── 2. WEEKLY ASTROLOGY EVENTS ───────────────────────────────────────────────

def weekly_content() -> dict[str, Any]:
    """Generate weekly deep-dive astrology content.

    Uses narrator endpoint in weekly mode for long-form SEO content.
    Posts to blog + queues for email newsletter.
    """
    logger.info("TROP weekly content workflow starting")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Generate weekly synthesis via narrator
    result = _trop_request("narrator", body={
        "userHash": "trop-squad-weekly",
        "tier": "pro",
        "type": "weekly",
        "weekStart": today,
        "systemPrompt": (
            "You are the cosmic narrator for The Realm of Patterns. "
            "Write a weekly astrology forecast in the style of Jungian depth psychology. "
            "Reference planetary transits, archetypal themes, and practical guidance. "
            "Format: title, introduction (SEO hook), 3-4 themed sections, closing reflection. "
            "1500+ words. Include: what planets are doing, what it means psychologically, how to work with it."
        ),
        "userPrompt": f"Write the weekly cosmic forecast for the week of {today}.",
    })

    narrative = result.get("narrative", "")
    if not narrative:
        logger.warning("Weekly narrative generation failed")
        return {"error": "narrative empty", "raw": result}

    # Publish to blog
    publish_result = _trop_request("publish", body={
        "content_type": "weekly_forecast",
        "language": "en",
        "title": f"This Week in the Patterns — {today}",
        "body": narrative,
        "params": {"week_start": today},
    })

    # Queue email newsletter
    # The remind endpoint handles individual emails; for newsletter we'd need batch
    # For MVP: store the content, Hadi triggers the newsletter send

    _store_in_mirror(
        f"TROP weekly forecast published for {today}: {len(narrative)} chars",
        f"trop-weekly-{today}",
    )

    logger.info("Weekly workflow complete: %d chars published", len(narrative))
    return {
        "date": today,
        "narrative_length": len(narrative),
        "published": publish_result,
    }


# ── 3. MONTHLY TAROT + I CHING ───────────────────────────────────────────────

def monthly_content() -> dict[str, Any]:
    """Generate monthly forecast using tarot archetypes + I Ching hexagrams."""
    logger.info("TROP monthly content workflow starting")
    today = datetime.now(timezone.utc)
    month_label = today.strftime("%B %Y")

    result = _trop_request("narrator", body={
        "userHash": "trop-squad-monthly",
        "tier": "pro",
        "type": "daily",  # Uses daily mode but with monthly prompt
        "systemPrompt": (
            "You are the cosmic oracle for The Realm of Patterns. "
            "Write a monthly forecast combining tarot archetypes and I Ching hexagrams. "
            "Map the 8 inner dimensions to 8 cards/hexagrams. "
            "For each dimension: card name, hexagram, interpretation, practical advice. "
            "Style: Jungian depth + accessible mysticism. 2000+ words. "
            "Include: monthly theme, key dates, shadow work, growth opportunity."
        ),
        "userPrompt": f"Write the monthly cosmic forecast for {month_label}.",
    })

    narrative = result.get("narrative", "")
    if not narrative:
        return {"error": "narrative empty"}

    publish_result = _trop_request("publish", body={
        "content_type": "monthly_forecast",
        "language": "en",
        "title": f"Monthly Patterns — {month_label}",
        "body": narrative,
        "params": {"month": today.strftime("%Y-%m")},
    })

    _store_in_mirror(
        f"TROP monthly forecast published for {month_label}: {len(narrative)} chars",
        f"trop-monthly-{today.strftime('%Y-%m')}",
    )

    logger.info("Monthly workflow complete: %d chars", len(narrative))
    return {"month": month_label, "narrative_length": len(narrative), "published": publish_result}


# ── 4. SOCIAL AUTOMATION ─────────────────────────────────────────────────────

def social_post() -> dict[str, Any]:
    """Generate and post short-form social content. Deterministic first, LLM optional."""
    logger.info("TROP social posting workflow")

    # Deterministic post from ephemeris (always works)
    from sos.squads.trop.ephemeris import get_today, generate_social_post

    data = get_today()
    post_text = generate_social_post(data)
    hashtags = "#astrology #cosmicweather #therealmofpatterns #jungian"

    # Post to Twitter
    twitter_result = _trop_request("share", body={
        "platform": "twitter",
        "text": f"{post_text}\n\n{hashtags}",
    })

    return {
        "post": post_text,
        "cosmic": {
            "sun": data["sun_sign"],
            "moon": data["moon_sign"],
            "phase": data["moon_phase"]["name"],
        },
        "twitter": twitter_result,
    }


# ── 5. HEALTH CHECK ──────────────────────────────────────────────────────────

def health_check() -> dict[str, Any]:
    """Check TROP infrastructure health. Alert on failure."""
    logger.info("TROP health check")
    checks: dict[str, dict] = {}

    # Site live?
    try:
        resp = requests.get(TROP_BASE, timeout=15)
        checks["site"] = {"status": "ok" if resp.status_code == 200 else "down", "code": resp.status_code}
    except Exception as exc:
        checks["site"] = {"status": "down", "error": str(exc)[:100]}

    # Narrator endpoint?
    narrator = _trop_request("narrator", body={
        "userHash": "health-check",
        "tier": "free",
        "type": "daily",
        "systemPrompt": "Reply with exactly: healthy",
        "userPrompt": "health check",
    })
    checks["narrator"] = {"status": "ok" if narrator.get("narrative") else "down"}

    # Alert if anything is down
    down = [k for k, v in checks.items() if v.get("status") != "ok"]
    if down:
        alert_text = f"TROP HEALTH ALERT: {', '.join(down)} down. Details: {json.dumps(checks)}"
        logger.error(alert_text)

        # Alert Hadi via bus
        try:
            import redis as redis_lib
            pw = os.environ.get("REDIS_PASSWORD", "")
            r = redis_lib.from_url(
                f"redis://:{pw}@localhost:6379/0" if pw else "redis://localhost:6379/0",
                decode_responses=True,
            )
            r.xadd("sos:stream:global:agent:hadi", {
                "source": "trop-squad",
                "type": "health_alert",
                "data": json.dumps({"text": alert_text, "source": "trop-squad"}),
            }, maxlen=500)
            r.publish("sos:wake:hadi", json.dumps({"source": "trop-squad", "text": alert_text}))
        except Exception:
            pass

    all_ok = len(down) == 0
    logger.info("TROP health: %s", "all OK" if all_ok else f"{len(down)} issues")
    return {"status": "ok" if all_ok else "degraded", "checks": checks}
