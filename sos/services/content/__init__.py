"""
SOS Content Service

Sovereign content management and marketing automation.
Integrates:
- gdrive-cms: Google Docs → MDX sync
- marketing_standup: Daily progress reports
- cms_pages: Supabase content storage
- Content strategy & calendar

Run as service:
    python -m sos.services.content
    uvicorn sos.services.content.app:app --port 8020

API Endpoints:
    GET  /health       - Service health
    GET  /strategy     - Content strategy
    GET  /calendar     - Editorial calendar
    POST /publish      - Publish content
"""

from sos.services.content.strategy import (
    ContentStrategy,
    ContentPillar,
    Audience,
    ContentFormat,
    MUMEGA_STRATEGY,
)
from sos.services.content.calendar import ContentCalendar, ScheduledPost, PostStatus
from sos.services.content.gdrive import GDriveCMS
from sos.services.content.publisher import ContentPublisher, get_publisher

__all__ = [
    # Strategy
    "ContentStrategy",
    "ContentPillar",
    "Audience",
    "ContentFormat",
    "MUMEGA_STRATEGY",
    # Calendar
    "ContentCalendar",
    "ScheduledPost",
    "PostStatus",
    # Integrations
    "GDriveCMS",
    "ContentPublisher",
    "get_publisher",
]
