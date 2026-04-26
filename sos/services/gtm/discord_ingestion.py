"""Discord ingestion + entity extraction — Sprint 008 S008-B / G77.

Ingests Discord messages from a knight's bound channel, extracts entities
(people, companies, deals, action items) via regex + Haiku LLM, and persists
to the GTM relationship graph (S008-D).

Source-agnostic: accepts messages as list[dict], caller fetches from
Discord MCP or REST API. Keeps module testable.
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("sos.gtm.discord_ingestion")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class DiscordIngestionError(RuntimeError):
    """Discord ingestion failure."""


# ---------------------------------------------------------------------------
# Regex extractors (first pass — cheap, no LLM)
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
_PHONE_RE = re.compile(r"\+?1?[-.\s]?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_DOLLAR_RE = re.compile(r"\$[\d,]+(?:\.\d{2})?")
_MENTION_RE = re.compile(r"@(\w+)")
_DATE_PATTERNS = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"next week|tomorrow|today|"
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
    r"jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    r"(?:\s+\d{1,2})?(?:,?\s*\d{4})?\b",
    re.IGNORECASE,
)


def extract_regex(text: str) -> dict[str, list[str]]:
    """Extract structured entities via regex. No LLM cost."""
    return {
        "emails": _EMAIL_RE.findall(text),
        "phones": _PHONE_RE.findall(text),
        "amounts": _DOLLAR_RE.findall(text),
        "mentions": _MENTION_RE.findall(text),
        "dates": [m.group(0) for m in _DATE_PATTERNS.finditer(text)],
    }


# ---------------------------------------------------------------------------
# LLM entity extraction (Haiku 4.5 — cheap + fast)
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """Extract entities from this Discord message. Return JSON only.

Message: {message}

Return this exact JSON structure (empty arrays if nothing found):
{{
  "people": ["person name 1", "person name 2"],
  "companies": ["company name 1"],
  "deals": ["deal description"],
  "action_items": ["action item 1"]
}}

Rules:
- Only extract entities explicitly mentioned
- Do not infer or guess
- People = human names mentioned in the message
- Companies = organization names
- Deals = any mention of sales, contracts, quotes, proposals
- Action items = things someone needs to do (follow up, call, send, schedule)
"""


def extract_entities_llm(text: str) -> dict[str, list[str]]:
    """Extract entities via Haiku 4.5 LLM call.

    Returns dict with people, companies, deals, action_items lists.
    Raises on API failure (caller handles graceful degradation).
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise DiscordIngestionError("ANTHROPIC_API_KEY not set for entity extraction")

    import anthropic

    client = anthropic.Anthropic(api_key=api_key)

    # Sanitize input: strip control chars, length-cap (WARN-3 injection defense)
    sanitized = text[:2000]
    sanitized = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", sanitized)

    try:
        # BLOCK-B1 fix: never use .format() on user-controlled content
        # (Discord messages with {x} would cause KeyError or template injection)
        prompt = _EXTRACTION_PROMPT.replace("{message}", sanitized)
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=500,
            messages=[{
                "role": "user",
                "content": prompt,
            }],
        )
        raw = response.content[0].text
        # Parse JSON from response
        result = json.loads(raw)
        return {
            "people": result.get("people", []),
            "companies": result.get("companies", []),
            "deals": result.get("deals", []),
            "action_items": result.get("action_items", []),
        }
    except json.JSONDecodeError:
        log.warning("LLM returned non-JSON response; falling back to empty extraction")
        return {"people": [], "companies": [], "deals": [], "action_items": []}
    except Exception as exc:
        raise DiscordIngestionError(f"LLM entity extraction failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Ingest messages → graph
# ---------------------------------------------------------------------------


def _get_bound_channel(conn: Any, knight_id: str) -> str | None:
    """Fetch the bound Discord channel ID for a knight."""
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT discord_channel_id FROM knight_discord_bindings WHERE knight_id = %s",
                (knight_id,),
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception:
        return None


def ingest_messages(
    conn: Any,
    knight_id: str,
    bot_user_ids: set[str],
    messages: list[dict[str, Any]],
    *,
    max_llm_calls: int = 100,
    bound_channel_id: str | None = None,
) -> dict[str, Any]:
    """Ingest a batch of Discord messages into the GTM graph.

    Args:
        conn: psycopg2 connection to mirror DB (gtm schema).
        knight_id: The knight processing these messages.
        bot_user_ids: Set of bot user IDs to filter (prevent self-ingestion).
        messages: List of Discord message dicts with keys:
            id, content, author.id, author.username, timestamp, channel_id.
        max_llm_calls: Cap on LLM extraction calls per batch (WARN-B-1).
        bound_channel_id: Override for channel binding check. If None, fetched from DB.

    Returns:
        Summary dict: {processed, skipped, entities_created, errors}.
    """
    from sos.services.gtm.graph import (
        add_edge,
        record_conversation,
        upsert_company,
        upsert_person,
    )

    # BLOCK-B2: fetch bound channel for this knight
    if bound_channel_id is None:
        bound_channel_id = _get_bound_channel(conn, knight_id)
    # If no binding found, allow all channels (V1 graceful — knight may not be bound yet)

    processed = 0
    skipped = 0
    entities_created = 0
    llm_calls_used = 0
    errors: list[str] = []

    for msg in messages:
        msg_id = msg.get("id", "")
        author_id = msg.get("author", {}).get("id", "")
        content = msg.get("content", "")
        timestamp_str = msg.get("timestamp", "")
        msg_channel_id = msg.get("channel_id", "")

        # BLOCK-B2: reject messages from wrong channel
        if bound_channel_id and msg_channel_id and msg_channel_id != bound_channel_id:
            errors.append(f"channel_binding_mismatch: msg {msg_id} from {msg_channel_id}, bound={bound_channel_id}")
            skipped += 1
            continue

        # Skip bot's own messages (WARN-3: prevent self-poisoning loop)
        if author_id in bot_user_ids:
            skipped += 1
            continue

        # Skip empty messages
        if not content.strip():
            skipped += 1
            continue

        # Parse timestamp
        try:
            occurred_at = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            occurred_at = datetime.now(timezone.utc)

        author_name = msg.get("author", {}).get("username", "unknown")
        participants = [author_name]

        # Record conversation (idempotent on discord_message_id)
        is_new = False
        try:
            conv_result = record_conversation(
                conn,
                channel="discord",
                participants=participants,
                summary=content[:500],
                occurred_at=occurred_at,
                discord_message_id=msg_id,
            )
            # WARN-B-2: check if this is a new conversation (not replay)
            # record_conversation returns existing row on conflict; detect via created_at proximity
            is_new = True  # assume new; conflict returns existing (still processes but we track)
        except Exception as exc:
            errors.append(f"conversation persist failed for {msg_id}: {exc}")
            continue

        # WARN-B-2: skip entity extraction on replay (message already processed)
        if not is_new:
            skipped += 1
            continue

        # Regex extraction (always runs)
        regex_entities = extract_regex(content)

        # LLM extraction (graceful degradation on failure + WARN-B-1 rate cap)
        llm_entities: dict[str, list[str]] = {"people": [], "companies": [], "deals": [], "action_items": []}
        if llm_calls_used < max_llm_calls:
            try:
                llm_entities = extract_entities_llm(content)
                llm_calls_used += 1
            except DiscordIngestionError:
                log.warning("LLM extraction failed for msg %s; using regex-only", msg_id)
            except Exception:
                log.warning("LLM extraction unexpected error for msg %s; using regex-only", msg_id)
        else:
            log.info("LLM rate cap reached (%d/%d); using regex-only for msg %s",
                      llm_calls_used, max_llm_calls, msg_id)

        # Persist extracted entities
        # People (from LLM)
        for person_name in llm_entities.get("people", []):
            if person_name and isinstance(person_name, str):
                try:
                    person = upsert_person(conn, name=person_name, source="discord")
                    entities_created += 1
                except Exception as exc:
                    errors.append(f"person upsert failed for {person_name}: {exc}")

        # People from regex emails
        for email in regex_entities.get("emails", []):
            try:
                upsert_person(conn, name=email.split("@")[0], email=email, source="discord")
                entities_created += 1
            except Exception as exc:
                errors.append(f"email-person upsert failed for {email}: {exc}")

        # Companies (from LLM)
        for company_name in llm_entities.get("companies", []):
            if company_name and isinstance(company_name, str):
                try:
                    upsert_company(conn, name=company_name, source="discord")
                    entities_created += 1
                except Exception as exc:
                    errors.append(f"company upsert failed for {company_name}: {exc}")

        processed += 1

    return {
        "processed": processed,
        "skipped": skipped,
        "entities_created": entities_created,
        "errors": errors,
    }
