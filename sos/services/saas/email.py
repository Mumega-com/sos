"""Email sending via Resend API."""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

log = logging.getLogger("sos.saas.email")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("RESEND_FROM_EMAIL", "Mumega <hello@mumega.com>")


async def send_email(to: str, subject: str, html: str) -> bool:
    """Send an email via Resend. Returns True on success."""
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping email to %s", to)
        return False

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": FROM_EMAIL,
                    "to": [to],
                    "subject": subject,
                    "html": html,
                },
            )
            if resp.status_code == 200:
                log.info("Email sent to %s: %s", to, subject)
                return True
            log.error("Email failed (%d): %s", resp.status_code, resp.text[:200])
            return False
    except Exception as exc:
        log.error("Email error: %s", exc)
        return False


async def send_welcome(to: str, name: str, mcp_url: str, slug: str) -> bool:
    """Send welcome email with MCP config."""
    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width: 600px; margin: 0 auto; background: #0A0A10; color: #EDEDF0; padding: 2rem; border-radius: 8px;">
      <h1 style="color: #D4A017; margin-bottom: 0.5rem;">Welcome to Mumega, {name}!</h1>
      <p>Your AI operating system is ready. Here's how to connect:</p>

      <h3 style="color: #06B6D4;">For Claude Code:</h3>
      <pre style="background: #151519; padding: 1rem; border-radius: 6px; overflow-x: auto; font-size: 0.85rem;">claude mcp add mumega --transport sse --url "{mcp_url}"</pre>

      <h3 style="color: #06B6D4;">For Claude Desktop / Cursor:</h3>
      <pre style="background: #151519; padding: 1rem; border-radius: 6px; overflow-x: auto; font-size: 0.85rem;">{{"mcpServers": {{"mumega": {{"url": "{mcp_url}"}}}}}}</pre>

      <h3>What to do next:</h3>
      <ol>
        <li>Paste the config above into your AI tool's settings</li>
        <li>Ask your AI: "What can you do with Mumega?"</li>
        <li>Say "remember that my business does X" to start building memory</li>
      </ol>

      <p style="color: rgba(255,255,255,0.5); font-size: 0.85rem; margin-top: 2rem;">
        Your site: https://{slug}.mumega.com<br>
        Dashboard: https://mumega.com/dashboard/
      </p>
    </div>
    """
    return await send_email(to, "Welcome to Mumega — Your AI is Ready", html)


async def send_magic_link(to: str, magic_url: str) -> bool:
    """Send magic link login email."""
    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width: 600px; margin: 0 auto; background: #0A0A10; color: #EDEDF0; padding: 2rem; border-radius: 8px;">
      <h1 style="color: #D4A017;">Log in to Mumega</h1>
      <p>Click the button below to log in. This link expires in 15 minutes.</p>
      <a href="{magic_url}" style="display: inline-block; background: #D4A017; color: #0A0A10; padding: 0.75rem 2rem; border-radius: 6px; text-decoration: none; font-weight: 600; margin: 1rem 0;">Log In</a>
      <p style="color: rgba(255,255,255,0.5); font-size: 0.85rem;">If you didn't request this, you can safely ignore this email.</p>
    </div>
    """
    return await send_email(to, "Your Mumega Login Link", html)


async def send_onboard_welcome(
    to: str, name: str, mcp_url: str, slug: str, site_url: str
) -> bool:
    """Send welcome email for onboarded customer with site URL."""
    html = f"""
    <div style="font-family: system-ui, sans-serif; max-width: 600px; margin: 0 auto; background: #0A0A10; color: #EDEDF0; padding: 2rem; border-radius: 8px;">
      <h1 style="color: #D4A017; margin-bottom: 0.5rem;">Welcome to Mumega, {name}!</h1>
      <p>Your site is being built at <strong>{site_url}</strong></p>

      <h3 style="color: #06B6D4; margin-top: 1.5rem;">Connect Your AI Tool</h3>
      <p>Copy one of these configs:</p>

      <h4>Claude Code:</h4>
      <pre style="background: #151519; padding: 1rem; border-radius: 6px; overflow-x: auto; font-size: 0.85rem;">claude mcp add mumega --transport sse --url "{mcp_url}"</pre>

      <h4>Claude Desktop / Cursor:</h4>
      <pre style="background: #151519; padding: 1rem; border-radius: 6px; overflow-x: auto; font-size: 0.85rem;">{{"mcpServers": {{"mumega": {{"url": "{mcp_url}"}}}}}}</pre>

      <h3 style="color: #06B6D4; margin-top: 1.5rem;">Next Steps</h3>
      <ol>
        <li>Paste the config into your AI tool's MCP settings</li>
        <li>Ask your AI: "What can you do with Mumega?"</li>
        <li>Start publishing content: "Create a blog post about [topic]"</li>
        <li>Your content appears on {site_url} instantly</li>
      </ol>

      <p style="color: rgba(255,255,255,0.5); font-size: 0.85rem; margin-top: 2rem;">
        Questions? Visit the dashboard: https://mumega.com/dashboard/<br>
        Need help? Reply to this email.
      </p>
    </div>
    """
    return await send_email(to, f"Your {name} Site is Live — Connect Your AI", html)
