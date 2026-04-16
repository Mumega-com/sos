"""Notification router — dispatches events to customer's preferred channels."""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("sos.saas.notifications")


class NotificationRouter:
    """Routes events to email, Telegram, webhook based on tenant preferences."""

    def __init__(self):
        self._preferences: dict[str, dict] = {}

    def set_preferences(self, tenant_slug: str, prefs: dict) -> None:
        """Set notification preferences for a tenant."""
        self._preferences[tenant_slug] = prefs
        # Also persist to DB
        import sqlite3

        db_path = Path.home() / ".sos" / "data" / "squads.db"
        conn = sqlite3.connect(str(db_path))
        try:
            # Ensure column exists first
            try:
                conn.execute(
                    "ALTER TABLE tenants ADD COLUMN notification_prefs TEXT"
                )
                conn.commit()
            except Exception:
                # Column might already exist, that's fine
                pass

            # Now update
            cursor = conn.execute(
                "UPDATE tenants SET notification_prefs = ? WHERE slug = ?",
                (json.dumps(prefs), tenant_slug),
            )
            conn.commit()
            log.debug("Updated notification preferences for %s", tenant_slug)
        except Exception as exc:
            log.warning("Failed to persist notification preferences for %s: %s", tenant_slug, exc)
        finally:
            conn.close()
        log.info("Notification preferences set for %s", tenant_slug)

    def get_preferences(self, tenant_slug: str) -> dict:
        """Get notification preferences. Defaults to email only."""
        if tenant_slug in self._preferences:
            return self._preferences[tenant_slug]

        # Try loading from DB
        import sqlite3

        db_path = Path.home() / ".sos" / "data" / "squads.db"
        try:
            conn = sqlite3.connect(str(db_path))
            cursor = conn.execute(
                "SELECT notification_prefs FROM tenants WHERE slug = ?",
                (tenant_slug,),
            )
            row = cursor.fetchone()
            conn.close()
            if row and row[0]:
                prefs = json.loads(row[0])
                self._preferences[tenant_slug] = prefs
                return prefs
        except Exception:
            # Column doesn't exist yet or other error — return defaults
            pass

        # Default preferences
        return {
            "email": True,
            "telegram": False,
            "webhook": None,
            "in_app": True,
        }

    async def notify(
        self,
        tenant_slug: str,
        event_type: str,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> dict:
        """Send notification to all enabled channels for a tenant."""
        prefs = self.get_preferences(tenant_slug)
        results = {}

        if prefs.get("email"):
            results["email"] = await self._send_email(tenant_slug, title, body)

        if prefs.get("telegram"):
            results["telegram"] = await self._send_telegram(tenant_slug, title, body)

        webhook_url = prefs.get("webhook")
        if webhook_url:
            results["webhook"] = await self._send_webhook(
                webhook_url, tenant_slug, event_type, title, body, data
            )

        return results

    async def _send_email(self, tenant_slug: str, title: str, body: str) -> bool:
        """Send via Resend."""
        try:
            from sos.services.saas.email import send_email
            from sos.services.saas.registry import TenantRegistry

            tenant = TenantRegistry().get(tenant_slug)
            if not tenant:
                return False
            html_body = f"<div style='font-family:system-ui;'><h2>{title}</h2><p>{body}</p></div>"
            await send_email(tenant.email, title, html_body)
            return True
        except Exception as exc:
            log.warning("Email notification failed for %s: %s", tenant_slug, exc)
            return False

    async def _send_telegram(self, tenant_slug: str, title: str, body: str) -> bool:
        """Send via Telegram bot."""
        try:
            import os

            import httpx

            bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
            # Look up chat_id from tenant record
            from sos.services.saas.registry import TenantRegistry

            tenant = TenantRegistry().get(tenant_slug)
            chat_id = tenant.telegram_chat_id if tenant else None
            if not bot_token or not chat_id:
                log.debug(
                    "Telegram config missing for %s (token=%s, chat_id=%s)",
                    tenant_slug,
                    "set" if bot_token else "not set",
                    "set" if chat_id else "not set",
                )
                return False
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": f"*{title}*\n{body}",
                        "parse_mode": "Markdown",
                    },
                )
                return resp.status_code == 200
        except Exception as exc:
            log.warning("Telegram notification failed for %s: %s", tenant_slug, exc)
            return False

    async def _send_webhook(
        self,
        url: str,
        tenant_slug: str,
        event_type: str,
        title: str,
        body: str,
        data: dict | None = None,
    ) -> bool:
        """Send webhook with HMAC signature."""
        try:
            import hashlib
            import hmac

            import httpx

            payload = json.dumps(
                {
                    "event": event_type,
                    "tenant": tenant_slug,
                    "title": title,
                    "body": body,
                    "data": data or {},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
            )
            # Sign with tenant slug as secret (proper secret management later)
            signature = hmac.new(
                tenant_slug.encode(), payload.encode(), hashlib.sha256
            ).hexdigest()
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    url,
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Mumega-Signature": signature,
                        "X-Mumega-Event": event_type,
                    },
                )
                return resp.status_code < 400
        except Exception as exc:
            log.warning("Webhook failed for %s: %s", tenant_slug, exc)
            return False


# Singleton
_router: NotificationRouter | None = None


def get_router() -> NotificationRouter:
    """Get or create the notification router singleton."""
    global _router
    if _router is None:
        _router = NotificationRouter()
    return _router


async def notify(
    tenant: str, event: str, title: str, body: str, data: dict | None = None
) -> dict:
    """Convenience function."""
    return await get_router().notify(tenant, event, title, body, data)
