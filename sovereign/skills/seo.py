"""
SEO skill implementations for the Mumega sovereign squad system.

Usage:
    python seo.py audit https://dentalnearyou.ca
    python seo.py meta https://dentalnearyou.ca
    python seo.py links https://dentalnearyou.ca
    python seo.py schema https://dentalnearyou.ca
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

sos_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "SOS"))
if os.path.isdir(sos_root) and sos_root not in sys.path:
    sys.path.insert(0, sos_root)

from sos.vendors.torivers_tools.progress import ConsoleProgressReporter

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RATE_LIMIT_DELAY = 1.0  # seconds between requests
REQUEST_TIMEOUT = 15.0  # seconds
USER_AGENT = "MumegaSEOBot/1.0 (+https://mumega.com/bot)"

TITLE_MIN = 50
TITLE_MAX = 60
DESC_MIN = 150
DESC_MAX = 160

DENTAL_SCHEMAS = [
    "Dentist",
    "MedicalBusiness",
    "LocalBusiness",
    "Organization",
    "WebSite",
    "BreadcrumbList",
    "FAQPage",
    "Review",
    "AggregateRating",
]

# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------


def _resolve_reporter(reporter: Any | None = None) -> Any:
    return reporter if reporter is not None else ConsoleProgressReporter(use_colors=False)


def _report(reporter: Any, message: str, *, success: bool = False) -> None:
    if success and hasattr(reporter, "log_success"):
        reporter.log_success(message)
        return
    if hasattr(reporter, "log_action"):
        reporter.log_action(message)
        return
    if hasattr(reporter, "log_info"):
        reporter.log_info(message)


def _make_client() -> httpx.AsyncClient:
    # Keep direct httpx here. The vendored ToRivers HTTP client routes through the
    # SDK proxy and adds HTTPS-only sandbox policy, but it is not a drop-in
    # replacement for this local async crawler path and does not add built-in retry
    # semantics beyond what we already control with rate limiting.
    return httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )


async def _fetch(client: httpx.AsyncClient, url: str) -> tuple[int, str, float]:
    """Fetch url; return (status_code, text, elapsed_seconds)."""
    start = time.monotonic()
    try:
        resp = await client.get(url)
        elapsed = time.monotonic() - start
        return resp.status_code, resp.text, elapsed
    except httpx.RequestError as exc:
        elapsed = time.monotonic() - start
        return 0, str(exc), elapsed


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _is_same_origin(base: str, href: str) -> bool:
    try:
        full = urljoin(base, href)
        return urlparse(full).netloc == urlparse(base).netloc
    except Exception:
        return False


async def _check_robots(base_url: str, client: httpx.AsyncClient) -> dict[str, Any]:
    robots_url = f"{_origin(base_url)}/robots.txt"
    status, text, _ = await _fetch(client, robots_url)
    present = status == 200
    sitemaps: list[str] = []
    allows_crawl = True
    if present:
        for line in text.splitlines():
            if line.lower().startswith("sitemap:"):
                sitemaps.append(line.split(":", 1)[1].strip())
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        allows_crawl = rp.can_fetch(USER_AGENT, base_url)
    return {
        "present": present,
        "url": robots_url,
        "sitemaps_listed": sitemaps,
        "allows_crawl": allows_crawl,
    }


async def _check_sitemap(base_url: str, client: httpx.AsyncClient) -> dict[str, Any]:
    sitemap_url = f"{_origin(base_url)}/sitemap.xml"
    status, text, _ = await _fetch(client, sitemap_url)
    present = status == 200
    url_count = 0
    if present:
        url_count = text.count("<url>")
    return {"present": present, "url": sitemap_url, "url_count": url_count}


def _extract_jsonld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    schemas: list[dict[str, Any]] = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, list):
                schemas.extend(data)
            else:
                schemas.append(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return schemas


# ---------------------------------------------------------------------------
# 1. site_audit
# ---------------------------------------------------------------------------


async def site_audit(url: str, reporter: Any | None = None) -> dict[str, Any]:
    """
    Fetch the live site and return a structured SEO audit report.

    Returns a dict with keys: url, status, response_time_s, meta, headings,
    canonical, og_tags, robots, sitemap, schema, issues.
    """
    reporter = _resolve_reporter(reporter)
    _report(reporter, "Fetching homepage...")

    async with _make_client() as client:
        robots_task = asyncio.create_task(_check_robots(url, client))
        sitemap_task = asyncio.create_task(_check_sitemap(url, client))

        status, html, elapsed = await _fetch(client, url)
        await asyncio.sleep(RATE_LIMIT_DELAY)

        robots = await robots_task
        sitemap = await sitemap_task

    issues: list[str] = []

    if status == 0:
        _report(reporter, "Found 1 issues", success=True)
        return {
            "url": url,
            "status": 0,
            "error": html,
            "response_time_s": elapsed,
            "issues": ["Site unreachable"],
        }

    soup = _soup(html)
    _report(reporter, "Checking meta tags...")

    # Title
    title_tag = soup.find("title")
    title_text = title_tag.get_text(strip=True) if title_tag else ""
    title_len = len(title_text)
    if not title_text:
        issues.append("Missing <title> tag")
    elif title_len < TITLE_MIN:
        issues.append(f"Title too short ({title_len} chars, min {TITLE_MIN})")
    elif title_len > TITLE_MAX:
        issues.append(f"Title too long ({title_len} chars, max {TITLE_MAX})")

    # Meta description
    desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
    desc_text = desc_tag["content"].strip() if desc_tag and desc_tag.get("content") else ""
    desc_len = len(desc_text)
    if not desc_text:
        issues.append("Missing meta description")
    elif desc_len < DESC_MIN:
        issues.append(f"Meta description too short ({desc_len} chars, min {DESC_MIN})")
    elif desc_len > DESC_MAX:
        issues.append(f"Meta description too long ({desc_len} chars, max {DESC_MAX})")

    # OG tags
    og_tags: dict[str, str] = {}
    for tag in soup.find_all("meta", property=re.compile("^og:", re.I)):
        prop = tag.get("property", "")
        content = tag.get("content", "")
        og_tags[prop] = content
    for required_og in ("og:title", "og:description", "og:image", "og:url"):
        if required_og not in og_tags:
            issues.append(f"Missing OG tag: {required_og}")

    # H1
    h1_tags = soup.find_all("h1")
    h1_texts = [h.get_text(strip=True) for h in h1_tags]
    if not h1_tags:
        issues.append("Missing H1 tag")
    elif len(h1_tags) > 1:
        issues.append(f"Multiple H1 tags found ({len(h1_tags)})")

    # H2 hierarchy
    h2_tags = [h.get_text(strip=True) for h in soup.find_all("h2")]

    # Canonical
    canonical_tag = soup.find("link", rel=re.compile("^canonical$", re.I))
    canonical_href = canonical_tag["href"] if canonical_tag and canonical_tag.get("href") else ""
    if not canonical_href:
        issues.append("Missing canonical URL")

    # JSON-LD
    schemas = _extract_jsonld(soup)
    schema_types = [s.get("@type", "Unknown") for s in schemas]
    if not schemas:
        issues.append("No JSON-LD schema markup found")

    if not robots["present"]:
        issues.append("robots.txt not found")
    if not sitemap["present"]:
        issues.append("sitemap.xml not found")
    if elapsed > 3.0:
        issues.append(f"Slow response time ({elapsed:.2f}s — target < 3s)")

    issue_count = len(issues)
    _report(reporter, f"Found {issue_count} issues", success=True)

    return {
        "url": url,
        "status": status,
        "response_time_s": round(elapsed, 3),
        "meta": {
            "title": title_text,
            "title_length": title_len,
            "description": desc_text,
            "description_length": desc_len,
        },
        "headings": {
            "h1": h1_texts,
            "h2": h2_tags[:10],  # first 10
        },
        "canonical": canonical_href,
        "og_tags": og_tags,
        "robots": robots,
        "sitemap": sitemap,
        "schema": {
            "types_found": schema_types,
            "count": len(schemas),
        },
        "issues": issues,
        "issue_count": issue_count,
    }


# ---------------------------------------------------------------------------
# 2. meta_optimizer
# ---------------------------------------------------------------------------


async def _discover_pages(base_url: str, client: httpx.AsyncClient) -> list[str]:
    """
    Discover crawlable pages from sitemap.xml, falling back to homepage links.
    Returns deduplicated list of same-origin URLs (max 50).
    """
    sitemap_url = f"{_origin(base_url)}/sitemap.xml"
    status, text, _ = await _fetch(client, sitemap_url)
    await asyncio.sleep(RATE_LIMIT_DELAY)

    urls: list[str] = [base_url]

    if status == 200:
        for loc in re.findall(r"<loc>(.*?)</loc>", text, re.DOTALL):
            loc = loc.strip()
            if _is_same_origin(base_url, loc):
                urls.append(loc)
        return list(dict.fromkeys(urls))[:50]

    # Fallback: crawl homepage links
    _, html, _ = await _fetch(client, base_url)
    await asyncio.sleep(RATE_LIMIT_DELAY)
    soup = _soup(html)
    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_url, href)
        if _is_same_origin(base_url, full) and full not in urls:
            urls.append(full)

    return list(dict.fromkeys(urls))[:50]


async def meta_optimizer(
    url: str, pages: list[str] | None = None
) -> dict[str, Any]:
    """
    Crawl key pages and analyze title/description quality.

    Returns actionable recommendations as JSON.
    """
    async with _make_client() as client:
        target_pages = pages if pages else await _discover_pages(url, client)

        results: list[dict[str, Any]] = []
        titles_seen: dict[str, list[str]] = {}
        descs_seen: dict[str, list[str]] = {}

        for page_url in target_pages:
            status, html, _ = await _fetch(client, page_url)
            await asyncio.sleep(RATE_LIMIT_DELAY)
            if status != 200:
                results.append({"url": page_url, "status": status, "error": "Non-200 response"})
                continue

            soup = _soup(html)
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            desc_tag = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
            desc = desc_tag["content"].strip() if desc_tag and desc_tag.get("content") else ""

            recommendations: list[str] = []

            if not title:
                recommendations.append("Add a <title> tag")
            elif len(title) < TITLE_MIN:
                recommendations.append(
                    f"Title too short ({len(title)} chars). Expand to {TITLE_MIN}-{TITLE_MAX} chars."
                )
            elif len(title) > TITLE_MAX:
                recommendations.append(
                    f"Title too long ({len(title)} chars). Trim to {TITLE_MIN}-{TITLE_MAX} chars."
                )

            if not desc:
                recommendations.append("Add a meta description")
            elif len(desc) < DESC_MIN:
                recommendations.append(
                    f"Description too short ({len(desc)} chars). Expand to {DESC_MIN}-{DESC_MAX} chars."
                )
            elif len(desc) > DESC_MAX:
                recommendations.append(
                    f"Description too long ({len(desc)} chars). Trim to {DESC_MIN}-{DESC_MAX} chars."
                )

            # Track duplicates
            if title:
                titles_seen.setdefault(title, []).append(page_url)
            if desc:
                descs_seen.setdefault(desc, []).append(page_url)

            results.append(
                {
                    "url": page_url,
                    "status": status,
                    "title": title,
                    "title_length": len(title),
                    "description": desc,
                    "description_length": len(desc),
                    "recommendations": recommendations,
                }
            )

    duplicate_titles = {t: urls for t, urls in titles_seen.items() if len(urls) > 1}
    duplicate_descs = {d: urls for d, urls in descs_seen.items() if len(urls) > 1}

    summary_issues: list[str] = []
    if duplicate_titles:
        summary_issues.append(
            f"{len(duplicate_titles)} duplicate title(s) found across pages"
        )
    if duplicate_descs:
        summary_issues.append(
            f"{len(duplicate_descs)} duplicate description(s) found across pages"
        )

    total_recs = sum(len(r.get("recommendations", [])) for r in results)

    return {
        "base_url": url,
        "pages_analyzed": len(results),
        "total_recommendations": total_recs,
        "duplicate_titles": duplicate_titles,
        "duplicate_descriptions": duplicate_descs,
        "summary_issues": summary_issues,
        "pages": results,
    }


# ---------------------------------------------------------------------------
# 3. internal_link_analyzer
# ---------------------------------------------------------------------------


async def internal_link_analyzer(
    url: str, pages: list[str] | None = None
) -> dict[str, Any]:
    """
    Crawl pages and map internal link structure.

    Returns link map, orphan pages, under/over-linked pages, and suggestions.
    """
    async with _make_client() as client:
        target_pages = pages if pages else await _discover_pages(url, client)

        # link_map[page] = list of outbound internal links
        link_map: dict[str, list[str]] = {}
        # inbound_count[page] = number of pages linking to it
        inbound_count: dict[str, int] = {p: 0 for p in target_pages}

        page_set = set(target_pages)

        for page_url in target_pages:
            status, html, _ = await _fetch(client, page_url)
            await asyncio.sleep(RATE_LIMIT_DELAY)
            if status != 200:
                link_map[page_url] = []
                continue

            soup = _soup(html)
            outbound: list[str] = []
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if not href or href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
                    continue
                full = urljoin(page_url, href)
                # Normalize: strip fragment
                full = full.split("#")[0].rstrip("/")
                normalized_base = page_url.rstrip("/")
                if full == normalized_base:
                    continue
                if _is_same_origin(url, full) and full not in outbound:
                    outbound.append(full)
                    if full in inbound_count:
                        inbound_count[full] += 1
                    elif full in page_set:
                        inbound_count[full] += 1

            link_map[page_url] = outbound

    # Orphans: pages with 0 inbound links (excluding the homepage itself)
    homepage_normalized = url.rstrip("/")
    orphans = [
        p for p, count in inbound_count.items()
        if count == 0 and p.rstrip("/") != homepage_normalized
    ]

    # Under-linked: 1-2 inbound, more than 3 pages total
    under_linked = [
        p for p, count in inbound_count.items()
        if 1 <= count <= 2 and len(target_pages) > 3
    ]

    # Over-linked: more than 10 outbound links (potential dilution)
    over_linked = [
        p for p, links in link_map.items()
        if len(links) > 10
    ]

    suggestions: list[str] = []
    if orphans:
        suggestions.append(
            f"{len(orphans)} orphan page(s) detected — add internal links to: "
            + ", ".join(orphans[:5])
        )
    if under_linked:
        suggestions.append(
            f"{len(under_linked)} page(s) have very few inbound links — consider linking from related content"
        )
    if over_linked:
        suggestions.append(
            f"{len(over_linked)} page(s) have 10+ outbound links — consider pruning to focus link equity"
        )
    if not suggestions:
        suggestions.append("Internal link structure looks healthy")

    return {
        "base_url": url,
        "pages_crawled": len(link_map),
        "orphan_pages": orphans,
        "under_linked_pages": under_linked,
        "over_linked_pages": over_linked,
        "suggestions": suggestions,
        "link_map": {
            page: {
                "outbound_count": len(links),
                "inbound_count": inbound_count.get(page, 0),
                "outbound_links": links[:20],  # cap for readability
            }
            for page, links in link_map.items()
        },
    }


# ---------------------------------------------------------------------------
# 4. schema_checker
# ---------------------------------------------------------------------------


def _validate_local_business(schema: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    required = ["name", "address", "telephone", "url", "openingHours"]
    for field in required:
        if field not in schema:
            missing.append(field)
    return missing


def _validate_organization(schema: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    required = ["name", "url", "logo", "contactPoint"]
    for field in required:
        if field not in schema:
            missing.append(field)
    return missing


def _validate_website(schema: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    for field in ("name", "url", "potentialAction"):
        if field not in schema:
            missing.append(field)
    return missing


_SCHEMA_VALIDATORS: dict[str, Any] = {
    "LocalBusiness": _validate_local_business,
    "Dentist": _validate_local_business,
    "MedicalBusiness": _validate_local_business,
    "Organization": _validate_organization,
    "WebSite": _validate_website,
}


async def schema_checker(url: str) -> dict[str, Any]:
    """
    Check JSON-LD schema markup on the page.

    Validates required fields and suggests missing schemas for dental sites.
    """
    async with _make_client() as client:
        status, html, elapsed = await _fetch(client, url)

    if status == 0:
        return {"url": url, "status": 0, "error": html, "issues": ["Site unreachable"]}

    soup = _soup(html)
    schemas = _extract_jsonld(soup)

    schema_reports: list[dict[str, Any]] = []
    found_types: list[str] = []

    for schema in schemas:
        schema_type = schema.get("@type", "Unknown")
        found_types.append(schema_type)
        validator = _SCHEMA_VALIDATORS.get(schema_type)
        missing_fields: list[str] = []
        if validator:
            missing_fields = validator(schema)

        schema_reports.append(
            {
                "type": schema_type,
                "missing_required_fields": missing_fields,
                "valid": len(missing_fields) == 0,
            }
        )

    # Suggest missing schemas relevant to dental/healthcare
    missing_recommended: list[str] = []
    for rec in DENTAL_SCHEMAS:
        if rec not in found_types:
            missing_recommended.append(rec)

    # Priority suggestions
    suggestions: list[str] = []
    if "Dentist" not in found_types and "MedicalBusiness" not in found_types:
        suggestions.append(
            "Add Dentist or MedicalBusiness schema — critical for local SEO and rich results"
        )
    if "LocalBusiness" not in found_types and "Dentist" not in found_types:
        suggestions.append("Add LocalBusiness schema with NAP (name, address, phone)")
    if "WebSite" not in found_types:
        suggestions.append("Add WebSite schema with SearchAction for sitelinks search box")
    if "FAQPage" not in found_types:
        suggestions.append("Add FAQPage schema to target FAQ rich results in search")
    if "AggregateRating" not in found_types:
        suggestions.append("Add AggregateRating schema to display star ratings in SERPs")

    issues: list[str] = []
    for report in schema_reports:
        if not report["valid"]:
            issues.append(
                f"{report['type']} schema missing fields: {', '.join(report['missing_required_fields'])}"
            )
    if not schemas:
        issues.append("No JSON-LD schema markup found on page")

    return {
        "url": url,
        "status": status,
        "schemas_found": len(schemas),
        "types_found": found_types,
        "schema_reports": schema_reports,
        "missing_recommended_types": missing_recommended,
        "suggestions": suggestions,
        "issues": issues,
        "issue_count": len(issues),
    }


# ---------------------------------------------------------------------------
# run_full_audit
# ---------------------------------------------------------------------------


async def run_full_audit(url: str, reporter: Any | None = None) -> dict[str, Any]:
    """
    Run all 4 SEO checks and return a combined report.
    """
    reporter = _resolve_reporter(reporter)

    _report(reporter, "Fetching homepage...")
    audit = await site_audit(url, reporter=reporter)
    await asyncio.sleep(RATE_LIMIT_DELAY)

    _report(reporter, "Checking meta tags...")
    meta = await meta_optimizer(url)
    await asyncio.sleep(RATE_LIMIT_DELAY)

    _report(reporter, "Analyzing internal links...")
    links = await internal_link_analyzer(url)
    await asyncio.sleep(RATE_LIMIT_DELAY)

    _report(reporter, "Checking schema markup...")
    schema = await schema_checker(url)

    total_issues = (
        audit.get("issue_count", 0)
        + meta.get("total_recommendations", 0)
        + len(links.get("orphan_pages", []))
        + schema.get("issue_count", 0)
    )

    _report(reporter, f"Found {total_issues} issues", success=True)

    return {
        "url": url,
        "total_issues": total_issues,
        "site_audit": audit,
        "meta_optimizer": meta,
        "internal_link_analyzer": links,
        "schema_checker": schema,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _print_json(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, default=str))


def _usage() -> None:
    print(
        "Usage:\n"
        "  python seo.py audit  <url>\n"
        "  python seo.py meta   <url>\n"
        "  python seo.py links  <url>\n"
        "  python seo.py schema <url>\n"
        "  python seo.py full   <url>\n"
    )


def main() -> None:
    if len(sys.argv) < 3:
        _usage()
        sys.exit(1)

    command = sys.argv[1].lower()
    url = sys.argv[2]

    dispatch: dict[str, Any] = {
        "audit": site_audit,
        "meta": meta_optimizer,
        "links": internal_link_analyzer,
        "schema": schema_checker,
        "full": run_full_audit,
    }

    if command not in dispatch:
        print(f"Unknown command: {command}")
        _usage()
        sys.exit(1)

    result = asyncio.run(dispatch[command](url))
    _print_json(result)


if __name__ == "__main__":
    main()
