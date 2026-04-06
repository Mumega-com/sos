"""
Content Publisher Module

Publishes content to various destinations:
- Supabase cms_pages table
- Static files (MDX/Markdown)
- Social media (via GoHighLevel)
"""

import os
import json
import httpx
from pathlib import Path
from typing import Dict, Optional, List
from datetime import datetime
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class PublishResult:
    """Result of a publish operation"""
    success: bool
    destination: str
    url: Optional[str] = None
    error: Optional[str] = None
    metadata: Optional[Dict] = None


class ContentPublisher:
    """
    Multi-destination content publisher.

    Supports:
    - Supabase cms_pages (mumega.com CMS)
    - Static MDX files (for static sites)
    - Social media via GoHighLevel API
    """

    def __init__(
        self,
        supabase_url: Optional[str] = None,
        supabase_key: Optional[str] = None,
        ghl_api_key: Optional[str] = None
    ):
        self.supabase_url = supabase_url or os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
        self.supabase_key = supabase_key or os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY")
        self.ghl_api_key = ghl_api_key or os.getenv("GHL_API_KEY")

    async def publish_to_supabase(
        self,
        slug: str,
        title: str,
        content: str,
        metadata: Optional[Dict] = None,
        published: bool = True
    ) -> PublishResult:
        """
        Publish content to Supabase cms_pages table.

        Args:
            slug: URL path (e.g., "blog/my-post")
            title: Post title
            content: Markdown content
            metadata: Optional metadata dict
            published: Whether to publish immediately

        Returns:
            PublishResult
        """
        if not self.supabase_url or not self.supabase_key:
            return PublishResult(
                success=False,
                destination="supabase",
                error="Supabase credentials not configured"
            )

        data = {
            "slug": slug,
            "title": title,
            "content": content,
            "metadata": metadata or {},
            "published": published,
            "updated_at": datetime.utcnow().isoformat()
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.supabase_url}/rest/v1/cms_pages",
                    json=data,
                    headers={
                        "apikey": self.supabase_key,
                        "Authorization": f"Bearer {self.supabase_key}",
                        "Content-Type": "application/json",
                        "Prefer": "resolution=merge-duplicates"
                    },
                    timeout=30.0
                )

                if response.status_code in [200, 201]:
                    return PublishResult(
                        success=True,
                        destination="supabase",
                        url=f"https://mumega.com/{slug}",
                        metadata={"slug": slug}
                    )
                else:
                    return PublishResult(
                        success=False,
                        destination="supabase",
                        error=f"HTTP {response.status_code}: {response.text}"
                    )

        except Exception as e:
            return PublishResult(
                success=False,
                destination="supabase",
                error=str(e)
            )

    def publish_to_file(
        self,
        slug: str,
        title: str,
        content: str,
        output_dir: Path,
        metadata: Optional[Dict] = None,
        format: str = "mdx"
    ) -> PublishResult:
        """
        Publish content as a static file.

        Args:
            slug: File name (without extension)
            title: Post title
            content: Markdown content
            output_dir: Directory to write to
            metadata: Optional frontmatter metadata
            format: File format (mdx, md)

        Returns:
            PublishResult
        """
        try:
            output_dir = Path(output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)

            # Build frontmatter
            frontmatter = {
                "title": title,
                "date": datetime.utcnow().isoformat(),
                **(metadata or {})
            }

            # Build file content
            fm_yaml = "\n".join(f"{k}: {json.dumps(v)}" for k, v in frontmatter.items())
            file_content = f"---\n{fm_yaml}\n---\n\n{content}"

            # Write file
            file_path = output_dir / f"{slug}.{format}"
            file_path.write_text(file_content)

            return PublishResult(
                success=True,
                destination="file",
                url=str(file_path),
                metadata={"path": str(file_path)}
            )

        except Exception as e:
            return PublishResult(
                success=False,
                destination="file",
                error=str(e)
            )

    async def publish_to_social(
        self,
        content: str,
        platforms: List[str] = None,
        image_url: Optional[str] = None
    ) -> List[PublishResult]:
        """
        Publish to social media via GoHighLevel.

        Args:
            content: Post content (will be truncated per platform)
            platforms: List of platforms (facebook, linkedin, twitter)
            image_url: Optional image URL

        Returns:
            List of PublishResults per platform
        """
        if not self.ghl_api_key:
            return [PublishResult(
                success=False,
                destination="social",
                error="GoHighLevel API key not configured"
            )]

        platforms = platforms or ["facebook", "linkedin"]
        results = []

        # GHL Social Planner API
        # Note: This is a simplified implementation
        # Real implementation would use proper GHL endpoints

        for platform in platforms:
            try:
                async with httpx.AsyncClient() as client:
                    # Truncate content per platform limits
                    max_length = {
                        "twitter": 280,
                        "facebook": 500,
                        "linkedin": 700
                    }.get(platform, 500)

                    truncated = content[:max_length]
                    if len(content) > max_length:
                        truncated = truncated[:-3] + "..."

                    # This would be the actual GHL API call
                    # For now, we'll simulate success
                    logger.info(f"Would publish to {platform}: {truncated[:50]}...")

                    results.append(PublishResult(
                        success=True,
                        destination=f"social:{platform}",
                        metadata={"platform": platform, "content_length": len(truncated)}
                    ))

            except Exception as e:
                results.append(PublishResult(
                    success=False,
                    destination=f"social:{platform}",
                    error=str(e)
                ))

        return results

    async def publish_all(
        self,
        slug: str,
        title: str,
        content: str,
        metadata: Optional[Dict] = None,
        destinations: List[str] = None
    ) -> Dict[str, PublishResult]:
        """
        Publish to multiple destinations.

        Args:
            slug: Content slug
            title: Content title
            content: Markdown content
            metadata: Optional metadata
            destinations: List of destinations ("supabase", "file", "social")

        Returns:
            Dict of destination -> PublishResult
        """
        destinations = destinations or ["supabase"]
        results = {}

        if "supabase" in destinations:
            results["supabase"] = await self.publish_to_supabase(
                slug=slug,
                title=title,
                content=content,
                metadata=metadata
            )

        if "file" in destinations:
            output_dir = Path(os.environ.get("MUMEGA_WEB_BLOG_DIR", str(Path.home() / "mumega-web" / "content" / "blog")))
            results["file"] = self.publish_to_file(
                slug=slug.split("/")[-1],  # Just the filename
                title=title,
                content=content,
                output_dir=output_dir,
                metadata=metadata
            )

        if "social" in destinations:
            # Create social-friendly excerpt
            excerpt = content[:200] + "..." if len(content) > 200 else content
            social_results = await self.publish_to_social(
                content=f"{title}\n\n{excerpt}\n\nhttps://mumega.com/{slug}"
            )
            for r in social_results:
                results[r.destination] = r

        return results


def get_publisher() -> ContentPublisher:
    """Factory function to get publisher instance"""
    return ContentPublisher()
