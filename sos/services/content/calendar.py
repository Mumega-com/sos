"""
Content Calendar Module

Manages scheduled content and editorial calendar.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
import json
import uuid


class PostStatus(Enum):
    PLANNED = "planned"
    DRAFTING = "drafting"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass
class ScheduledPost:
    """A scheduled piece of content"""
    id: str
    title: str
    pillar_id: str
    format: str
    target_audience: str
    scheduled_date: datetime
    status: PostStatus = PostStatus.PLANNED

    # Content details
    brief: Optional[str] = None
    draft_content: Optional[str] = None
    final_content: Optional[str] = None

    # Metadata
    keywords: List[str] = field(default_factory=list)
    image_url: Optional[str] = None
    slug: Optional[str] = None

    # Tracking
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    published_at: Optional[datetime] = None
    published_url: Optional[str] = None

    # Agent tracking
    assigned_agent: Optional[str] = None
    generation_attempts: int = 0

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "title": self.title,
            "pillar_id": self.pillar_id,
            "format": self.format,
            "target_audience": self.target_audience,
            "scheduled_date": self.scheduled_date.isoformat(),
            "status": self.status.value,
            "brief": self.brief,
            "draft_content": self.draft_content,
            "final_content": self.final_content,
            "keywords": self.keywords,
            "image_url": self.image_url,
            "slug": self.slug,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "published_url": self.published_url,
            "assigned_agent": self.assigned_agent,
            "generation_attempts": self.generation_attempts
        }

    @classmethod
    def from_dict(cls, data: Dict) -> "ScheduledPost":
        return cls(
            id=data["id"],
            title=data["title"],
            pillar_id=data["pillar_id"],
            format=data["format"],
            target_audience=data["target_audience"],
            scheduled_date=datetime.fromisoformat(data["scheduled_date"]),
            status=PostStatus(data.get("status", "planned")),
            brief=data.get("brief"),
            draft_content=data.get("draft_content"),
            final_content=data.get("final_content"),
            keywords=data.get("keywords", []),
            image_url=data.get("image_url"),
            slug=data.get("slug"),
            created_at=datetime.fromisoformat(data["created_at"]) if data.get("created_at") else datetime.utcnow(),
            updated_at=datetime.fromisoformat(data["updated_at"]) if data.get("updated_at") else datetime.utcnow(),
            published_at=datetime.fromisoformat(data["published_at"]) if data.get("published_at") else None,
            published_url=data.get("published_url"),
            assigned_agent=data.get("assigned_agent"),
            generation_attempts=data.get("generation_attempts", 0)
        )


class ContentCalendar:
    """Editorial calendar for content planning"""

    def __init__(self, storage_path: Optional[Path] = None):
        self.storage_path = storage_path or Path.home() / ".sos" / "content_calendar.json"
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        self.posts: List[ScheduledPost] = []
        self._load()

    def _load(self):
        """Load calendar from disk"""
        if self.storage_path.exists():
            with open(self.storage_path) as f:
                data = json.load(f)
                self.posts = [ScheduledPost.from_dict(p) for p in data.get("posts", [])]

    def _save(self):
        """Save calendar to disk"""
        data = {"posts": [p.to_dict() for p in self.posts]}
        with open(self.storage_path, "w") as f:
            json.dump(data, f, indent=2)

    def add_post(self, post: ScheduledPost) -> ScheduledPost:
        """Add a new scheduled post"""
        self.posts.append(post)
        self._save()
        return post

    def create_post(
        self,
        title: str,
        pillar_id: str,
        format: str,
        target_audience: str,
        scheduled_date: datetime,
        **kwargs
    ) -> ScheduledPost:
        """Create and add a new post"""
        post = ScheduledPost(
            id=str(uuid.uuid4())[:8],
            title=title,
            pillar_id=pillar_id,
            format=format,
            target_audience=target_audience,
            scheduled_date=scheduled_date,
            **kwargs
        )
        return self.add_post(post)

    def get_post(self, post_id: str) -> Optional[ScheduledPost]:
        """Get post by ID"""
        return next((p for p in self.posts if p.id == post_id), None)

    def update_post(self, post_id: str, **updates) -> Optional[ScheduledPost]:
        """Update a post"""
        post = self.get_post(post_id)
        if post:
            for key, value in updates.items():
                if hasattr(post, key):
                    setattr(post, key, value)
            post.updated_at = datetime.utcnow()
            self._save()
        return post

    def update_status(self, post_id: str, status: PostStatus) -> Optional[ScheduledPost]:
        """Update post status"""
        return self.update_post(post_id, status=status)

    def get_upcoming(self, days: int = 7) -> List[ScheduledPost]:
        """Get posts scheduled in the next N days"""
        cutoff = datetime.utcnow() + timedelta(days=days)
        return [
            p for p in self.posts
            if p.scheduled_date <= cutoff and p.status not in [PostStatus.PUBLISHED, PostStatus.FAILED]
        ]

    def get_ready_to_publish(self) -> List[ScheduledPost]:
        """Get posts that are approved and past their scheduled date"""
        now = datetime.utcnow()
        return [
            p for p in self.posts
            if p.status == PostStatus.APPROVED and p.scheduled_date <= now
        ]

    def get_needs_draft(self) -> List[ScheduledPost]:
        """Get posts that need content generation"""
        return [p for p in self.posts if p.status == PostStatus.PLANNED]

    def get_by_status(self, status: PostStatus) -> List[ScheduledPost]:
        """Get all posts with a specific status"""
        return [p for p in self.posts if p.status == status]

    def get_by_pillar(self, pillar_id: str) -> List[ScheduledPost]:
        """Get all posts for a pillar"""
        return [p for p in self.posts if p.pillar_id == pillar_id]

    def generate_week_plan(
        self,
        strategy: "ContentStrategy",
        start_date: Optional[datetime] = None,
        posts_count: int = 3
    ) -> List[ScheduledPost]:
        """Auto-generate a week's content plan based on strategy"""
        from sos.services.content.strategy import ContentStrategy

        start = start_date or datetime.utcnow()
        created = []

        # Rotate through pillars and audiences
        pillar_cycle = iter(strategy.pillars * 10)  # Enough to cover
        audience_cycle = iter(strategy.audiences * 10)

        for i in range(posts_count):
            pillar = next(pillar_cycle)
            audience = next(audience_cycle)
            format_choice = pillar.formats[0] if pillar.formats else "blog_post"

            scheduled = start + timedelta(days=(i * 2) + 1)  # Every 2 days

            post = self.create_post(
                title=f"[DRAFT] {pillar.name} for {audience.name}",
                pillar_id=pillar.id,
                format=format_choice.value if hasattr(format_choice, 'value') else format_choice,
                target_audience=audience.id,
                scheduled_date=scheduled,
                keywords=pillar.keywords[:5]
            )
            created.append(post)

        return created

    def get_calendar_view(self, weeks: int = 4) -> Dict[str, List[ScheduledPost]]:
        """Get calendar view grouped by week"""
        now = datetime.utcnow()
        result = {}

        for week in range(weeks):
            week_start = now + timedelta(weeks=week)
            week_end = week_start + timedelta(days=7)
            week_key = f"Week {week + 1}: {week_start.strftime('%b %d')} - {week_end.strftime('%b %d')}"

            result[week_key] = [
                p for p in self.posts
                if week_start <= p.scheduled_date < week_end
            ]

        return result

    def get_stats(self) -> Dict:
        """Get calendar statistics"""
        return {
            "total": len(self.posts),
            "by_status": {
                status.value: len([p for p in self.posts if p.status == status])
                for status in PostStatus
            },
            "upcoming_7_days": len(self.get_upcoming(7)),
            "ready_to_publish": len(self.get_ready_to_publish()),
            "needs_draft": len(self.get_needs_draft())
        }
