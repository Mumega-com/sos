"""test_calcifer_post_index.py — LOCK-S027-C-1.1

Four cases for `index_mumega_posts()`:
  1. Populated dir with mix of published / draft / no-status — count + max date
  2. Empty dir → (0, None)
  3. Missing dir → (0, None) + warning logged (no raise)
  4. Malformed frontmatter on one file → skipped, others counted (no raise)
"""
from __future__ import annotations

from pathlib import Path

import pytest

from sos.services.health.calcifer import index_mumega_posts


def _write_post(dir_path: Path, name: str, frontmatter: str, body: str = "post body") -> None:
    (dir_path / name).write_text(f"---\n{frontmatter}\n---\n\n{body}\n", encoding="utf-8")


def test_populated_mix_counts_only_published_and_returns_max_date(tmp_path: Path) -> None:
    blog = tmp_path / "blog"
    blog.mkdir()
    _write_post(
        blog,
        "2026-01-01-old-published.md",
        'title: "Old Published"\ndate: "2026-01-01"\nstatus: "published"',
    )
    _write_post(
        blog,
        "2026-05-01-new-published.md",
        'title: "New Published"\ndate: "2026-05-01"\nstatus: "published"',
    )
    _write_post(
        blog,
        "2026-04-01-draft.md",
        'title: "Draft"\ndate: "2026-04-01"\nstatus: "draft"',
    )
    _write_post(
        blog,
        "2026-03-01-no-status.md",
        'title: "No Status"\ndate: "2026-03-01"',
    )

    result = index_mumega_posts(blog)

    assert result["mumega_posts_total"] == 2
    # Lexicographic compare on ISO-8601 strings = chronological compare.
    assert result["mumega_posts_last_publish_ts"] == "2026-05-01"


def test_empty_dir_returns_zero_and_null(tmp_path: Path) -> None:
    blog = tmp_path / "blog"
    blog.mkdir()

    result = index_mumega_posts(blog)

    assert result == {"mumega_posts_total": 0, "mumega_posts_last_publish_ts": None}


def test_missing_dir_returns_zero_and_null_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"
    # Sanity
    assert not missing.exists()

    # MUST NOT raise.
    result = index_mumega_posts(missing)

    assert result == {"mumega_posts_total": 0, "mumega_posts_last_publish_ts": None}


def test_malformed_frontmatter_is_skipped_not_fatal(tmp_path: Path) -> None:
    blog = tmp_path / "blog"
    blog.mkdir()
    # Valid published post.
    _write_post(
        blog,
        "good.md",
        'title: "Good"\ndate: "2026-05-01"\nstatus: "published"',
    )
    # Malformed YAML (unmatched bracket).
    (blog / "bad.md").write_text(
        "---\ntitle: [oops\nstatus: published\ndate: 2026-04-01\n---\n\nbody\n",
        encoding="utf-8",
    )
    # No closing fence at all.
    (blog / "no-fence.md").write_text(
        "---\ntitle: no fence\nstatus: published\n",
        encoding="utf-8",
    )

    # MUST NOT raise; bad files skipped, good one counted.
    result = index_mumega_posts(blog)

    assert result["mumega_posts_total"] == 1
    assert result["mumega_posts_last_publish_ts"] == "2026-05-01"


def test_published_post_without_date_does_not_set_max(tmp_path: Path) -> None:
    """Edge: a published post with no `date` field still counts in total but
    contributes nothing to `mumega_posts_last_publish_ts`."""
    blog = tmp_path / "blog"
    blog.mkdir()
    _write_post(
        blog,
        "no-date.md",
        'title: "No Date"\nstatus: "published"',
    )

    result = index_mumega_posts(blog)
    assert result["mumega_posts_total"] == 1
    assert result["mumega_posts_last_publish_ts"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
