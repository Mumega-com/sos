"""
Content Strategy Module

Defines content pillars, audiences, and strategic guidelines
for autonomous content generation.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
from enum import Enum
from pathlib import Path
import json
import yaml


class ContentFormat(Enum):
    BLOG_POST = "blog_post"
    TECHNICAL_DEEP_DIVE = "technical_deep_dive"
    HOW_TO_GUIDE = "how_to_guide"
    CASE_STUDY = "case_study"
    VISIONARY_ESSAY = "visionary_essay"
    INDUSTRY_ANALYSIS = "industry_analysis"
    PRODUCT_UPDATE = "product_update"
    TRANSMISSION = "transmission"  # Short-form updates


@dataclass
class Audience:
    """Target audience segment"""
    id: str
    name: str
    description: str
    pain_points: List[str]
    goals: List[str]
    channels: List[str]  # Where they consume content
    tone: str  # How to speak to them


@dataclass
class ContentPillar:
    """Strategic content pillar/theme"""
    id: str
    name: str
    description: str
    keywords: List[str]
    formats: List[ContentFormat]
    target_audiences: List[str]  # Audience IDs
    examples: List[str]


@dataclass
class ContentStrategy:
    """Complete content marketing strategy"""
    brand_voice: str
    mission: str
    vision: str
    pillars: List[ContentPillar]
    audiences: List[Audience]
    seo_keywords: List[str]
    competitors: List[str]

    # Publishing guidelines
    posts_per_week: int = 3
    optimal_length: Dict[str, int] = field(default_factory=lambda: {
        "blog_post": 1200,
        "technical_deep_dive": 2500,
        "how_to_guide": 1500,
        "transmission": 300
    })

    @classmethod
    def load(cls, path: Path) -> "ContentStrategy":
        """Load strategy from YAML file"""
        with open(path) as f:
            data = yaml.safe_load(f)

        pillars = [
            ContentPillar(
                id=p["id"],
                name=p["name"],
                description=p["description"],
                keywords=p.get("keywords", []),
                formats=[ContentFormat(f) for f in p.get("formats", ["blog_post"])],
                target_audiences=p.get("audiences", []),
                examples=p.get("examples", [])
            )
            for p in data.get("pillars", [])
        ]

        audiences = [
            Audience(
                id=a["id"],
                name=a["name"],
                description=a["description"],
                pain_points=a.get("pain_points", []),
                goals=a.get("goals", []),
                channels=a.get("channels", []),
                tone=a.get("tone", "professional")
            )
            for a in data.get("audiences", [])
        ]

        return cls(
            brand_voice=data.get("brand_voice", ""),
            mission=data.get("mission", ""),
            vision=data.get("vision", ""),
            pillars=pillars,
            audiences=audiences,
            seo_keywords=data.get("seo_keywords", []),
            competitors=data.get("competitors", []),
            posts_per_week=data.get("posts_per_week", 3)
        )

    def save(self, path: Path):
        """Save strategy to YAML file"""
        data = {
            "brand_voice": self.brand_voice,
            "mission": self.mission,
            "vision": self.vision,
            "posts_per_week": self.posts_per_week,
            "seo_keywords": self.seo_keywords,
            "competitors": self.competitors,
            "pillars": [
                {
                    "id": p.id,
                    "name": p.name,
                    "description": p.description,
                    "keywords": p.keywords,
                    "formats": [f.value for f in p.formats],
                    "audiences": p.target_audiences,
                    "examples": p.examples
                }
                for p in self.pillars
            ],
            "audiences": [
                {
                    "id": a.id,
                    "name": a.name,
                    "description": a.description,
                    "pain_points": a.pain_points,
                    "goals": a.goals,
                    "channels": a.channels,
                    "tone": a.tone
                }
                for a in self.audiences
            ]
        }
        with open(path, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True)

    def get_pillar(self, pillar_id: str) -> Optional[ContentPillar]:
        """Get pillar by ID"""
        return next((p for p in self.pillars if p.id == pillar_id), None)

    def get_audience(self, audience_id: str) -> Optional[Audience]:
        """Get audience by ID"""
        return next((a for a in self.audiences if a.id == audience_id), None)

    def generate_brief(self, pillar_id: str, format: ContentFormat, audience_id: str) -> Dict:
        """Generate a content brief for agents"""
        pillar = self.get_pillar(pillar_id)
        audience = self.get_audience(audience_id)

        if not pillar or not audience:
            raise ValueError(f"Invalid pillar or audience: {pillar_id}, {audience_id}")

        return {
            "pillar": pillar.name,
            "format": format.value if hasattr(format, 'value') else format,
            "target_length": self.optimal_length.get(format.value if hasattr(format, 'value') else format, 1000),
            "keywords": pillar.keywords[:5],
            "audience": {
                "name": audience.name,
                "tone": audience.tone,
                "pain_points": audience.pain_points[:3],
                "goals": audience.goals[:3]
            },
            "brand_voice": self.brand_voice,
            "guidelines": f"""
Write for {audience.name}.
Tone: {audience.tone}
Focus on: {', '.join(pillar.keywords[:3])}
Address pain points: {', '.join(audience.pain_points[:2])}
Help them achieve: {', '.join(audience.goals[:2])}
"""
        }


# Default Mumega Content Strategy
MUMEGA_STRATEGY = ContentStrategy(
    brand_voice="Confident, technical, slightly rebellious. We're building the future of work, not another SaaS tool. Speak to builders and visionaries.",
    mission="Deploy sovereign AI employees that work FOR you, not FOR Big Tech.",
    vision="A world where every business has access to enterprise-grade AI workforce.",
    pillars=[
        ContentPillar(
            id="sovereign-ai",
            name="Sovereign AI",
            description="Local-first, privacy-respecting AI that you own and control",
            keywords=["sovereign AI", "local-first", "privacy", "self-hosted", "data ownership"],
            formats=[ContentFormat.BLOG_POST, ContentFormat.TECHNICAL_DEEP_DIVE],
            target_audiences=["enterprise-architects", "developers"],
            examples=["Why Your AI Should Work FOR You", "The Case Against AI Vendor Lock-in"]
        ),
        ContentPillar(
            id="ai-employees",
            name="AI Employees",
            description="Autonomous agents that work 24/7 as digital employees",
            keywords=["AI employees", "digital workforce", "automation", "autonomous agents"],
            formats=[ContentFormat.BLOG_POST, ContentFormat.CASE_STUDY, ContentFormat.HOW_TO_GUIDE],
            target_audiences=["sme-leaders", "consultants"],
            examples=["Hiring Your First AI Employee", "ROI of Digital Workers"]
        ),
        ContentPillar(
            id="multi-agent-systems",
            name="Multi-Agent Systems",
            description="Swarms, councils, and orchestrated AI teams",
            keywords=["multi-agent", "swarm intelligence", "AI orchestration", "agent teams"],
            formats=[ContentFormat.TECHNICAL_DEEP_DIVE, ContentFormat.VISIONARY_ESSAY],
            target_audiences=["developers", "enterprise-architects"],
            examples=["Building Sovereign Squads", "The Council Pattern"]
        ),
        ContentPillar(
            id="frc-framework",
            name="FRC Framework",
            description="Fractal Resonance Cognition - our theoretical foundation",
            keywords=["FRC", "consciousness", "coherence", "adaptive resonance"],
            formats=[ContentFormat.TECHNICAL_DEEP_DIVE, ContentFormat.VISIONARY_ESSAY],
            target_audiences=["researchers", "developers"],
            examples=["The Physics of AI Coherence", "16D Cognitive Architecture"]
        )
    ],
    audiences=[
        Audience(
            id="sme-leaders",
            name="SME Business Leaders",
            description="Owners and executives of small-medium enterprises looking to scale with AI",
            pain_points=["Can't afford enterprise AI", "Too many SaaS tools", "No technical team"],
            goals=["Reduce operational costs", "Scale without hiring", "Automate repetitive work"],
            channels=["LinkedIn", "Twitter/X", "Industry newsletters"],
            tone="Business-focused, ROI-driven, accessible"
        ),
        Audience(
            id="developers",
            name="Developers & Engineers",
            description="Technical builders interested in AI infrastructure",
            pain_points=["Vendor lock-in", "Complex integrations", "Black-box AI"],
            goals=["Build with open tools", "Understand the system", "Ship faster"],
            channels=["GitHub", "Hacker News", "Dev.to", "Discord"],
            tone="Technical, direct, code-first"
        ),
        Audience(
            id="consultants",
            name="Consultants & Coaches",
            description="Solo practitioners who want to scale their expertise",
            pain_points=["Selling time not value", "Limited bandwidth", "Repetitive questions"],
            goals=["Clone expertise", "Passive income", "Serve more clients"],
            channels=["LinkedIn", "Substack", "Podcasts"],
            tone="Empowering, practical, results-focused"
        ),
        Audience(
            id="enterprise-architects",
            name="Enterprise Architects",
            description="Technical decision makers at larger organizations",
            pain_points=["Security concerns", "Integration complexity", "Governance"],
            goals=["Safe AI adoption", "Scalable infrastructure", "Measurable outcomes"],
            channels=["Industry conferences", "Gartner reports", "LinkedIn"],
            tone="Professional, security-conscious, strategic"
        )
    ],
    seo_keywords=[
        "AI employees",
        "sovereign AI",
        "autonomous agents",
        "local-first AI",
        "multi-agent systems",
        "AI automation",
        "digital workforce",
        "self-hosted AI"
    ],
    competitors=[
        "ChatGPT Enterprise",
        "Claude for Work",
        "Crew.ai",
        "AutoGPT",
        "LangChain"
    ],
    posts_per_week=3
)
