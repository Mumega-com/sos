"""TROP Visual Cards — HTML → screenshot → shareable images.

Generates cosmic weather cards for social media. No AI models.
Just HTML templates + Playwright screenshot + Telegram posting.

Usage:
    from sos.squads.trop.cards import generate_daily_card, post_card_to_telegram
    path = generate_daily_card(cosmic_data)
    post_card_to_telegram(path, "Today's cosmic weather")
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("sos.squads.trop.cards")

OUTPUT_DIR = Path.home() / ".sos" / "trop" / "cards"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

TROP_BOT_TOKEN = os.environ.get("TROP_TELEGRAM_BOT_TOKEN", "8682793155:AAGOiy0OGILgydZ7S_HrWB4e63gNOT4DC4U")
TROP_CHAT_ID = os.environ.get("TROP_TELEGRAM_CHAT_ID", "-1003713252924")

# Moon phase symbols
MOON_ICONS = {
    "New Moon": "🌑",
    "Waxing Crescent": "🌒",
    "First Quarter": "🌓",
    "Waxing Gibbous": "🌔",
    "Full Moon": "🌕",
    "Waning Gibbous": "🌖",
    "Last Quarter": "🌗",
    "Waning Crescent": "🌘",
}

SIGN_SYMBOLS = {
    "Aries": "♈", "Taurus": "♉", "Gemini": "♊", "Cancer": "♋",
    "Leo": "♌", "Virgo": "♍", "Libra": "♎", "Scorpio": "♏",
    "Sagittarius": "♐", "Capricorn": "♑", "Aquarius": "♒", "Pisces": "♓",
}


def _card_html(
    title: str,
    subtitle: str,
    body_lines: list[str],
    footer: str = "therealmofpatterns.com",
    icon: str = "✦",
) -> str:
    """Generate HTML for a cosmic card. Dark theme, gold accents."""
    body_html = "".join(f'<p class="body">{line}</p>' for line in body_lines)
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;width:1080px;height:1080px;background:#0B1020;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  font-family:'Georgia','Times New Roman',serif;color:#C9A227;overflow:hidden;">

  <div style="font-size:72px;margin-bottom:20px;">{icon}</div>

  <div style="font-size:42px;font-weight:bold;letter-spacing:3px;
    text-transform:uppercase;margin-bottom:8px;text-align:center;padding:0 60px;">
    {title}
  </div>

  <div style="font-size:24px;color:#8B7355;letter-spacing:2px;
    margin-bottom:40px;text-align:center;">
    {subtitle}
  </div>

  <div style="width:2px;height:40px;background:linear-gradient(#C9A227,transparent);
    margin-bottom:30px;"></div>

  <div style="max-width:800px;text-align:center;padding:0 80px;">
    {body_html}
  </div>

  <div style="width:2px;height:40px;background:linear-gradient(transparent,#C9A227);
    margin-top:30px;margin-bottom:20px;"></div>

  <div style="font-size:16px;color:#4A3F2F;letter-spacing:4px;
    text-transform:uppercase;position:absolute;bottom:40px;">
    ✦ {footer} ✦
  </div>

</body>
</html>
<style>
.body {{ font-size:26px; line-height:1.6; color:#D4C5A0; margin:8px 0; }}
</style>"""


async def _screenshot(html: str, output_path: Path) -> Path:
    """Render HTML to PNG using Playwright."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 1080, "height": 1080})
        await page.set_content(html)
        await page.screenshot(path=str(output_path), type="png")
        await browser.close()

    return output_path


def generate_daily_card(data: dict[str, Any] | None = None) -> Path:
    """Generate today's cosmic weather card as 1080x1080 PNG."""
    import asyncio

    if data is None:
        from sos.squads.trop.ephemeris import get_today
        data = get_today()

    date = data["date"]
    moon_sign = data["moon_sign"]
    sun_sign = data["sun_sign"]
    phase = data["moon_phase"]["name"]
    dominant = data["dominant"]["name"]
    signature = data["signature"]
    moon_icon = MOON_ICONS.get(phase, "✦")

    from sos.squads.trop.ephemeris import SIGN_ENERGY
    sun_energy = SIGN_ENERGY[sun_sign].split("—")[0].strip()
    moon_energy = SIGN_ENERGY[moon_sign].split("—")[0].strip()

    html = _card_html(
        title=f"{phase}",
        subtitle=f"Sun in {sun_sign} · Moon in {moon_sign}",
        body_lines=[
            f"Today's dominant pattern: {dominant}",
            "",
            f"☉ {sun_sign} — {sun_energy}",
            f"☽ {moon_sign} — {moon_energy}",
            "",
            f"Signature: {signature}",
        ],
        footer="therealmofpatterns.com",
        icon=moon_icon,
    )

    output = OUTPUT_DIR / f"daily-{date}.png"
    asyncio.run(_screenshot(html, output))
    logger.info("Daily card generated: %s", output)
    return output


def generate_sign_card(sign: str, data: dict[str, Any]) -> Path:
    """Generate a zodiac sign card."""
    import asyncio

    date = data["date"]
    phase = data["moon_phase"]["name"]
    moon_icon = MOON_ICONS.get(phase, "✦")
    sign_symbol = SIGN_SYMBOLS.get(sign, "✦")

    from sos.squads.trop.ephemeris import SIGN_ENERGY
    energy = SIGN_ENERGY.get(sign, "")

    html = _card_html(
        title=f"{sign_symbol} {sign}",
        subtitle=f"{phase} · {data['date']}",
        body_lines=[energy],
        icon=moon_icon,
    )

    output = OUTPUT_DIR / f"sign-{sign.lower()}-{date}.png"
    asyncio.run(_screenshot(html, output))
    return output


def generate_sign_cards(data: dict[str, Any] | None = None) -> list[Path]:
    """Generate all 12 zodiac sign cards."""
    if data is None:
        from sos.squads.trop.ephemeris import get_today
        data = get_today()

    paths = []
    for sign in SIGN_SYMBOLS:
        path = generate_sign_card(sign, data)
        paths.append(path)
        logger.info("Sign card: %s", path.name)
    return paths


def post_card_to_telegram(image_path: Path, caption: str = "") -> bool:
    """Post a card image to TROP Telegram channel."""
    import requests

    if not TROP_BOT_TOKEN or not TROP_CHAT_ID:
        return False

    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                f"https://api.telegram.org/bot{TROP_BOT_TOKEN}/sendPhoto",
                data={"chat_id": TROP_CHAT_ID, "caption": caption, "parse_mode": "Markdown"},
                files={"photo": f},
                timeout=30,
            )
        if resp.status_code == 200:
            logger.info("Card posted to Telegram: %s", image_path.name)
            return True
        logger.warning("Telegram photo post failed: %d %s", resp.status_code, resp.text[:100])
    except Exception as exc:
        logger.warning("Telegram photo error: %s", exc)
    return False
