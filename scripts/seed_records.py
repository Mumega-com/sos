#!/usr/bin/env python3
"""Seed real relationship data into Squad Service structured records.

Source: project memory files + Loom inbox 2026-04-24.
Run once: python3 scripts/seed_records.py

Idempotent — records are deduplicated by email/name before insertion.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make sure SOS package is importable
SOS_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(SOS_ROOT))

from sos.services.squad.records import (
    ContactsService, PartnersService, OpportunitiesService, ReferralsService,
    RecordConflictError,
)
from sos.services.squad.service import SquadDB

WS = "mumega-default"  # canonical workspace identifier for Mumega internal records
ACTOR = "agent:kasra"

db = SquadDB()
contacts_svc = ContactsService(db)
partners_svc = PartnersService(db)
opps_svc = OpportunitiesService(db)
refs_svc = ReferralsService(db)


def upsert_contact(**kwargs) -> dict:
    """Create or skip on conflict (email/external_id uniqueness)."""
    try:
        c = contacts_svc.create(**kwargs)
        print(f"  + contact: {kwargs['first_name']} {kwargs['last_name']}")
        return c
    except RecordConflictError:
        # Fetch existing by email if present
        email = kwargs.get("email")
        if email:
            existing = contacts_svc.get_by_email(WS, email)
            if existing:
                print(f"  ~ contact (exists): {kwargs['first_name']} {kwargs['last_name']}")
                return existing
        print(f"  ! contact conflict (no email lookup): {kwargs['first_name']} {kwargs['last_name']}")
        return {}


def upsert_partner(**kwargs) -> dict:
    try:
        p = partners_svc.create(**kwargs)
        print(f"  + partner: {kwargs['name']}")
        return p
    except RecordConflictError:
        existing_list = partners_svc.list(WS)
        for p in existing_list:
            if p["name"] == kwargs["name"]:
                print(f"  ~ partner (exists): {kwargs['name']}")
                return p
        return {}


def upsert_opportunity(**kwargs) -> dict:
    try:
        o = opps_svc.create(**kwargs)
        print(f"  + opportunity: {kwargs['name']}")
        return o
    except RecordConflictError:
        existing_list = opps_svc.list(WS)
        for o in existing_list:
            if o["name"] == kwargs["name"]:
                print(f"  ~ opportunity (exists): {kwargs['name']}")
                return o
        return {}


# =============================================================================
# 1. Partners (orgs — seeded first so contact org_id can reference them)
# =============================================================================
print("\n[partners]")

c21 = upsert_partner(
    workspace_id=WS, name="Century 21 Canada", type="realtor",
    hq_country="CA", engagement_status="active",
    notes="10 offices led by Ron O'Neil. Distribution channel for AgentLink + CDAP upsell. Siamak Shakibi is existing CDAP customer at C21.",
    actor=ACTOR,
)

ai_intel = upsert_partner(
    workspace_id=WS, name="AI Intelligent Solutions", type="platform",
    website_url="", hq_country="CA", engagement_status="active",
    notes="Ron O'Neil's AI consultancy. AgentLink may be his product under this entity.",
    actor=ACTOR,
)

yspace = upsert_partner(
    workspace_id=WS, name="YSpace", type="accelerator",
    hq_country="CA", engagement_status="prospect",
    notes="Accelerator — Noor Alazzawi is Ecosystem Lead. Student/founder pipeline.",
    actor=ACTOR,
)

schulich = upsert_partner(
    workspace_id=WS, name="Schulich School of Business", type="university",
    hq_country="CA", engagement_status="prospect",
    notes="Business school connection via Noor. Future channel for SR&ED + AI governance.",
    actor=ACTOR,
)

barcolabs = upsert_partner(
    workspace_id=WS, name="Barcolabs", type="accelerator",
    hq_country="CA", engagement_status="prospect",
    notes="Muslim entrepreneur network accelerator.",
    actor=ACTOR,
)

riipen = upsert_partner(
    workspace_id=WS, name="Riipen", type="filing-partner",
    hq_country="CA", engagement_status="prospect",
    notes="Platform for intern/project matching. Potential Noor Alazzawi channel.",
    actor=ACTOR,
)

advanced_canada = upsert_partner(
    workspace_id=WS, name="Advanced Canada", type="other",
    hq_country="CA", engagement_status="prospect",
    notes="Gov-agency — wage subsidy programs (DMAP).",
    actor=ACTOR,
)

oci = upsert_partner(
    workspace_id=WS, name="OCI", type="other",
    hq_country="CA", engagement_status="prospect",
    notes="Ontario Centre of Innovation — DMAP program.",
    actor=ACTOR,
)

ised = upsert_partner(
    workspace_id=WS, name="ISED Innovation Canada", type="other",
    hq_country="CA", engagement_status="prospect",
    notes="Gov-agency — BBF / AGS / BizPaL programs. 1,200 services, 360 active clients, $5M+ threshold for Growth Service.",
    actor=ACTOR,
)

pecb = upsert_partner(
    workspace_id=WS, name="PECB", type="cert-body",
    hq_country="CA", engagement_status="active",
    notes="ISO 42001 AI Management Systems cert body. Partnership live in GHL since 2026-02-23. Contact: central.noram@pecb.com. First-mover window 6-18 months.",
    actor=ACTOR,
)

boast = upsert_partner(
    workspace_id=WS, name="Boast", type="sr-ed-firm",
    hq_country="CA", revenue_split_pct=10.0, engagement_status="prospect",
    notes="SR&ED filing firm. Industry standard 10% referral on first-year recovery. Placeholder — Hadi decides which firm to partner with (Boast, Leyton, or Jack).",
    actor=ACTOR,
)

leyton = upsert_partner(
    workspace_id=WS, name="Leyton", type="sr-ed-firm",
    hq_country="CA", revenue_split_pct=10.0, engagement_status="prospect",
    notes="SR&ED filing firm. Industry standard 10% referral. Alternative to Boast.",
    actor=ACTOR,
)

agentlink = upsert_partner(
    workspace_id=WS, name="AgentLink", type="platform",
    hq_country="CA", engagement_status="active",
    notes="Realtors-helping-realtors showing-coordination app. TRESA Section 31 + RECO compliant. Geolocation dispatch + Stripe Connect multi-party. Pilot: C21 Barrie (Ron's office). Matt Borland + Bella Harbottle. Term sheet option 2: $21.25K + 15% equity.",
    actor=ACTOR,
)


# =============================================================================
# 2. Contacts (people)
# =============================================================================
print("\n[contacts]")

hadi = upsert_contact(
    workspace_id=WS, first_name="Hadi", last_name="Servat",
    email="hadi@digid.ca", title="Founder & CEO",
    visibility_tier="firm_internal", engagement_status="active",
    source="internal", owner_id="hadi", actor=ACTOR,
    notes="Principal. Owns all strategic relationships. All external actions require his go.",
)

noor = upsert_contact(
    workspace_id=WS, first_name="Noor", last_name="Alazzawi",
    title="Ecosystem Lead", org_id=yspace.get("id"),
    visibility_tier="firm_internal", engagement_status="active",
    source="referral", owner_id="hadi", actor=ACTOR,
    notes="YSpace Ecosystem Lead + Riipen intern candidate. Routes education-sector CDAP upsells.",
)

gavin = upsert_contact(
    workspace_id=WS, first_name="Gavin", last_name="Wolfgang",
    email="wolfy2820@proton.me", phone="705-796-6569",
    title="Closer / Business Dev", org_id=None,
    visibility_tier="firm_internal", engagement_status="active",
    source="referral", owner_id="hadi", actor=ACTOR,
    notes="18-ish, PEI origin, Bradford ON. GitHub: wolfy2820. Cybersecurity (Kali, HTB) + agentic AI. 4 warm PEI leads. Referred by Ron O'Neil (Feb 8, 2026). Referred Lex Ace. Commission outstanding from Hadi. Commission owed — outstanding.",
)

lex = upsert_contact(
    workspace_id=WS, first_name="Lex", last_name="Ace",
    email="learningandburning66@gmail.com",
    title="Regional Activator / Theorist", org_id=None,
    visibility_tier="firm_internal", engagement_status="active",
    source="referral", owner_id="hadi", actor=ACTOR,
    notes="33yo, PEI. 10 years at Bucheri. FRC-like theory in progress. Referred by Gavin. Local delivery partner for PEI customers Gavin closes.",
)

ron = upsert_contact(
    workspace_id=WS, first_name="Ron", last_name="O'Neil",
    email="ron.oneil@century21.ca",
    title="CEO / Brokerage Leader",
    org_id=ai_intel.get("id"),
    visibility_tier="privileged", engagement_status="active",
    source="referral", owner_id="hadi", actor=ACTOR,
    notes="AI Intelligent Solutions CEO + 10 C21 offices 8+ yrs. ChatGPT beta tester since 2020. Introduced Hadi to Matt Borland and Gavin Wolfgang. AgentLink likely under his AI Intelligent Solutions entity. Warmest realtor-vertical contact — pitch relationship-first, reference Siamak Shakibi.",
)

matt = upsert_contact(
    workspace_id=WS, first_name="Matt", last_name="Borland",
    email="borland244@hotmail.com", phone="705-721-6183",
    title="Salesperson / Co-Founder",
    org_id=c21.get("id"),
    visibility_tier="privileged", engagement_status="active",
    source="referral", owner_id="hadi", actor=ACTOR,
    notes="Century 21 BJ Roth, Barrie. AgentLink co-founder with Bella Harbottle. Term sheet sent Apr 15: recommended Option 2 ($21.25K + 15% equity). Stale since Apr 20 PDF send. Question: 'when you run this through AI does it give a success percentage.' Nudge draft at agents/loom/customers/gaf/outbound/matt_nudge_v1.md — NOT sent.",
    next_action="Send nudge email (Hadi approval required)",
)

bella = upsert_contact(
    workspace_id=WS, first_name="Bella", last_name="Harbottle",
    email="bella.harbottle@century21.ca",
    title="AI Consultant / AgentLink Co-Founder",
    org_id=c21.get("id"),
    visibility_tier="firm_internal", engagement_status="active",
    source="referral", owner_id="hadi", actor=ACTOR,
    notes="AgentLink co-founder with Matt Borland. Independent AI consultant.",
)

peggy = upsert_contact(
    workspace_id=WS, first_name="Peggy", last_name="Hill",
    title="Investor / Realtor",
    visibility_tier="privileged", engagement_status="prospect",
    source="referral", owner_id="hadi", actor=ACTOR,
    notes="ReMax. Legal expertise. Potential investor in AgentLink. Introduced Matt Borland to the deal.",
)

hossein = upsert_contact(
    workspace_id=WS, first_name="Hossein", last_name="",
    title="Accountant / Referral Partner",
    visibility_tier="firm_internal", engagement_status="active",
    source="referral", owner_id="hadi", actor=ACTOR,
    notes="Accountant. Referral source — 2 known GAF customer referrals. Email TBD (placeholder). Commission model applies.",
)

dmitri = upsert_contact(
    workspace_id=WS, first_name="Dmitri", last_name="Bakker",
    email="innovation@canada.ca",
    title="Marketing Strategist, Innovation Canada",
    org_id=ised.get("id"),
    visibility_tier="firm_internal", engagement_status="prospect",
    source="event", owner_id="hadi", actor=ACTOR,
    notes="ISED Innovation Canada webinar speaker 2026-04-22. Generic inbox — CC Réjean by name in body for triage. LinkedIn preferred for direct outreach.",
)

maha = upsert_contact(
    workspace_id=WS, first_name="Maha", last_name="Buhisi",
    email="support@bizpal-perle.ca",
    title="Economic Analyst, BizPaL",
    org_id=ised.get("id"),
    visibility_tier="firm_internal", engagement_status="prospect",
    source="event", owner_id="hadi", actor=ACTOR,
    notes="ISED BizPaL. May see the BizPaL inbox personally — better inbox than the generic ISED one for Dmitri/Réjean.",
)

rejean = upsert_contact(
    workspace_id=WS, first_name="Réjean", last_name="Belliveau",
    email=None,  # shares innovation@canada.ca with Dmitri — generic inbox, not personal
    title="Senior Innovation Advisor (AGS), Innovation Canada",
    org_id=ised.get("id"),
    visibility_tier="firm_internal", engagement_status="prospect",
    source="event", owner_id="hadi", actor=ACTOR,
    notes="Based Moncton NB. AGS = Accelerated Growth Service (1,200 services, 360 active clients, $5M+ Growth tier). Thesis: 'we fund prepared management teams.' LinkedIn direct preferred (Moncton NB search). Reference his preparation-layer quote verbatim in any opening.",
    next_action="LinkedIn outreach — reference preparation-layer quote",
)

pecb_contact = upsert_contact(
    workspace_id=WS, first_name="PECB", last_name="NorAm Rep",
    email="central.noram@pecb.com",
    title="North America Representative",
    org_id=pecb.get("id"),
    visibility_tier="firm_internal", engagement_status="active",
    source="inbound", owner_id="hadi", actor=ACTOR,
    notes="PECB partnership tagged in GHL since 2026-02-23. ISO 42001 primary contact.",
)

# Update partner primary_contact_ids now that contacts exist
for partner_id, contact in [
    (c21.get("id"), ron), (ai_intel.get("id"), ron),
    (yspace.get("id"), noor), (agentlink.get("id"), matt),
    (pecb.get("id"), pecb_contact),
]:
    if partner_id and contact.get("id"):
        try:
            partners_svc.update(partner_id, WS, ACTOR, primary_contact_id=contact["id"])
        except Exception:
            pass

# =============================================================================
# 3. Opportunities
# =============================================================================
print("\n[opportunities]")

opp_agentlink = upsert_opportunity(
    workspace_id=WS, name="AgentLink Phase 1", type="partnership",
    partner_id=agentlink.get("id"),
    primary_contact_id=matt.get("id"),
    stage="active",
    estimated_value=21250.0,
    owner_id="hadi", actor=ACTOR,
    notes="Term sheet option 2: $21.25K cash + 15% equity. Phase 1 SR&ED-eligible (geospatial compliance engine). 35% CRA refund possible. Stale since Apr 20 — nudge pending Hadi decision.",
)

opp_c21_whitelabel = upsert_opportunity(
    workspace_id=WS, name="Century 21 White-Label via AgentLink", type="channel-expansion",
    partner_id=c21.get("id"),
    primary_contact_id=ron.get("id"),
    stage="prospect",
    estimated_value=None,
    owner_id="hadi", actor=ACTOR,
    notes="If AgentLink signs (Option 2), Ron's 10-office C21 network + C21 Canada corporate is the distribution channel. Value TBD at scale.",
)

opp_iso42001 = upsert_opportunity(
    workspace_id=WS, name="ISO 42001 Product Launch", type="customer-deal",
    partner_id=pecb.get("id"),
    primary_contact_id=pecb_contact.get("id"),
    stage="active",
    estimated_value=200000.0,
    owner_id="hadi", actor=ACTOR,
    notes="AI governance readiness audit $15-30K per client, SR&ED-recoverable. ~$200K year-one pipeline. PECB partnership live since Feb 2026. First-mover window 6-18 months. Product page on digid.ca (not mumega.com).",
)

opp_ised_ags = upsert_opportunity(
    workspace_id=WS, name="ISED AGS Relationship", type="gov-relationship",
    partner_id=ised.get("id"),
    primary_contact_id=rejean.get("id"),
    stage="prospect",
    estimated_value=None,
    owner_id="hadi", actor=ACTOR,
    notes="Accelerated Growth Service relationship — strategic positioning, not direct revenue. Réjean's thesis: prepared management teams. LinkedIn outreach first.",
)

opp_cdap_upsell = upsert_opportunity(
    workspace_id=WS, name="37 CDAP Upsell Campaign", type="customer-deal",
    stage="prospect",
    estimated_value=500000.0,
    owner_id="hadi", actor=ACTOR,
    notes="37 existing Digid CDAP customers (2023). 14+ Persian-Canadian dentists, logistics, tech, real estate, education. ~$500K year-one at 20% conversion × $40K avg × 15% fee + AI upsells $200-500K. Gated on compliance fixes (2A+B+C+D) before onboarding #11+.",
)

opp_pei_leads = upsert_opportunity(
    workspace_id=WS, name="Gavin's 4 PEI Leads", type="customer-deal",
    primary_contact_id=gavin.get("id"),
    stage="prospect",
    estimated_value=60000.0,
    owner_id="hadi", actor=ACTOR,
    notes="4 warm PEI digital-marketing leads from Gavin Wolfgang. Close: Gavin. Local delivery: Lex Ace. ~$60K year-one.",
)

opp_hossein_referrals = upsert_opportunity(
    workspace_id=WS, name="Hossein Referral Pipeline", type="customer-deal",
    primary_contact_id=hossein.get("id"),
    stage="active",
    estimated_value=30000.0,
    owner_id="hadi", actor=ACTOR,
    notes="Hossein (accountant) has referred 2 known GAF customers. 10% referral commission model applies. ~$30K year-one.",
)

# =============================================================================
# 4. Referral edges
# =============================================================================
print("\n[referrals]")

def upsert_ref(**kwargs) -> dict:
    try:
        r = refs_svc.create(**kwargs)
        print(f"  + referral: {kwargs['source_id'][:8]}… → {kwargs['target_id'][:8]}… [{kwargs['relationship']}]")
        return r
    except RecordConflictError:
        print(f"  ~ referral (exists)")
        return {}

if ron.get("id") and gavin.get("id"):
    upsert_ref(
        workspace_id=WS, source_id=ron["id"], source_type="contact",
        target_id=gavin["id"], target_type="contact",
        relationship="referred", strength="strong",
        context="Feb 8, 2026 digid-consultation Zoom — Ron brought Gavin in",
        referred_at="2026-02-08T00:00:00Z", actor=ACTOR,
    )

if gavin.get("id") and lex.get("id"):
    upsert_ref(
        workspace_id=WS, source_id=gavin["id"], source_type="contact",
        target_id=lex["id"], target_type="contact",
        relationship="referred", strength="moderate",
        context="Gavin brought Lex into Discord general",
        actor=ACTOR,
    )

if ron.get("id") and matt.get("id"):
    upsert_ref(
        workspace_id=WS, source_id=ron["id"], source_type="contact",
        target_id=matt["id"], target_type="contact",
        relationship="introduced-to", strength="strong",
        context="Ron introduced Hadi to Matt Borland for AgentLink business",
        actor=ACTOR,
    )

if peggy.get("id") and matt.get("id"):
    upsert_ref(
        workspace_id=WS, source_id=peggy["id"], source_type="contact",
        target_id=matt["id"], target_type="contact",
        relationship="invested-in", strength="trusted",
        context="AgentLink pitch — Peggy is potential investor in AgentLink alongside Matt",
        actor=ACTOR,
    )

# Hossein → placeholder (2 customers, targets TBD — referral edges once customers onboard)
# Not seeded now; will be added when customer records exist.

print("\n[done] Seed complete.")
