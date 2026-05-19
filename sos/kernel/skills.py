"""
SOS Skills Loader - Load skills from OpenClaw vendor directory.

Skills are markdown files (SKILL.md) with YAML frontmatter containing:
- name: skill identifier
- version: semantic version
- description: trigger conditions (when to use)
- references: optional list of supporting docs

This module provides utilities to:
- List available skills
- Load skill content
- Search skills by keyword
- Get skill references
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Skill:
    """A loaded skill definition."""
    name: str
    version: str
    description: str
    content: str
    path: Path
    references: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "references": self.references,
            "path": str(self.path),
            "metadata": self.metadata,
        }


class SkillLoader:
    """
    Load skills from OpenClaw-compatible directory.

    Expected structure:
        skills_path/
        ├── skill-name/
        │   ├── SKILL.md          # Main skill definition
        │   └── references/       # Optional supporting docs
        │       └── *.md
    """

    def __init__(self, skills_path: Optional[str] = None):
        self.skills_path = Path(
            skills_path or os.environ.get("SOS_SKILLS_PATH") or Path.home() / ".agents" / "skills"
        )
        self._cache: Dict[str, Skill] = {}

    def list_skills(self) -> List[str]:
        """List all available skill names."""
        if not self.skills_path.exists():
            return []

        skills = []
        for item in self.skills_path.iterdir():
            if item.is_dir() and (item / "SKILL.md").exists():
                skills.append(item.name)
        return sorted(skills)

    def load(self, name: str) -> Optional[Skill]:
        """Load a skill by name."""
        if name in self._cache:
            return self._cache[name]

        skill_dir = self.skills_path / name
        skill_file = skill_dir / "SKILL.md"

        if not skill_file.exists():
            return None

        content = skill_file.read_text()
        frontmatter, body = self._parse_frontmatter(content)

        skill = Skill(
            name=frontmatter.get("name", name),
            version=frontmatter.get("version", "1.0.0"),
            description=frontmatter.get("description", ""),
            content=body,
            path=skill_file,
            references=frontmatter.get("references", []),
            metadata={k: v for k, v in frontmatter.items()
                     if k not in ("name", "version", "description", "references")},
        )

        self._cache[name] = skill
        return skill

    def load_with_references(self, name: str) -> Dict[str, Any]:
        """Load a skill with all its reference documents."""
        skill = self.load(name)
        if not skill:
            return {}

        result = {
            "skill": skill.to_dict(),
            "content": skill.content,
            "references": {},
        }

        # Load references if they exist
        refs_dir = skill.path.parent / "references"
        if refs_dir.exists():
            for ref_path in refs_dir.rglob("*.md"):
                rel_path = ref_path.relative_to(refs_dir)
                result["references"][str(rel_path)] = ref_path.read_text()

        return result

    def search(self, query: str, limit: int = 10) -> List[Skill]:
        """Search skills by keyword in name or description."""
        query_lower = query.lower()
        matches = []

        for name in self.list_skills():
            skill = self.load(name)
            if not skill:
                continue

            # Score based on matches
            score = 0
            if query_lower in skill.name.lower():
                score += 10
            if query_lower in skill.description.lower():
                score += 5

            if score > 0:
                matches.append((score, skill))

        # Sort by score descending
        matches.sort(key=lambda x: x[0], reverse=True)
        return [skill for _, skill in matches[:limit]]

    def get_skill_content(self, name: str) -> str:
        """Get just the content of a skill (for injection into context)."""
        skill = self.load(name)
        return skill.content if skill else ""

    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """Parse YAML frontmatter from markdown content."""
        # Match frontmatter between --- markers
        match = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)$', content, re.DOTALL)

        if not match:
            return {}, content

        frontmatter_str = match.group(1)
        body = match.group(2)

        # Simple YAML parsing (without external dependency)
        frontmatter = {}
        current_key = None
        current_list = None

        for line in frontmatter_str.split('\n'):
            line = line.rstrip()
            if not line or line.startswith('#'):
                continue

            # Check for list item
            if line.startswith('  - ') and current_key:
                if current_list is None:
                    current_list = []
                    frontmatter[current_key] = current_list
                current_list.append(line[4:].strip())
                continue

            # Check for key: value
            if ':' in line:
                key, _, value = line.partition(':')
                key = key.strip()
                value = value.strip()

                current_key = key
                current_list = None

                if value:
                    # Remove quotes if present
                    if (value.startswith('"') and value.endswith('"')) or \
                       (value.startswith("'") and value.endswith("'")):
                        value = value[1:-1]
                    frontmatter[key] = value

        return frontmatter, body


# Default loader instance
_default_loader: Optional[SkillLoader] = None


def get_loader(skills_path: Optional[str] = None) -> SkillLoader:
    """Get or create the default skill loader."""
    global _default_loader
    if _default_loader is None or skills_path:
        _default_loader = SkillLoader(skills_path)
    return _default_loader


def load_skill(name: str) -> Optional[Skill]:
    """Convenience function to load a skill."""
    return get_loader().load(name)


def list_skills() -> List[str]:
    """Convenience function to list skills."""
    return get_loader().list_skills()


def search_skills(query: str, limit: int = 10) -> List[Skill]:
    """Convenience function to search skills."""
    return get_loader().search(query, limit)
