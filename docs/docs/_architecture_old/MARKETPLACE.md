# SOS Marketplace — Plugins & Artifacts (Draft)

Status: Draft v0.1  
Owner: Codex  
Last Updated: 2026-01-10

## Purpose
Define how SOS supports an “app store” model for agent-created plugins/tools/templates, grounded in the Artifact Registry and the plugin trust model.

## Core Objects

### Artifact (from Artifact Registry)
- Immutable, versioned bundle of outputs
- Identified by a content hash (CID)
- Linked to a task/proof-of-work

### Plugin
- A specific kind of Artifact with a signed manifest
- Declares:
  - capabilities required (what it needs)
  - capabilities provided (what it offers)
  - sandbox requirements

### Listing
Marketplace metadata referencing an Artifact CID:
- name, description, category
- version(s) and CID(s)
- pricing model
- trust level + review status

## Trust & Safety
Align with `SECURITY_MODEL.md` trust levels:
- `core`: ships with SOS
- `verified`: signed by Mumega key, auto-approved
- `community`: author-signed, requires explicit approval in production
- `unsigned`: dev only (blocked in prod)

Mandatory controls:
- capability gating at install + at runtime
- sandboxing defaults: read-only FS, outbound network allowlist, resource limits
- automated static checks (manifest validity, declared permissions)

## Categories (v0.1)
- Tools (search, data, code execution)
- Adapters (telegram, discord, web integrations)
- Wallet adapters (solana, etc.)
- Memory augmenters (ranking, summarizers)
- Templates (prompts, policies, workflows)

## Pricing Models
Support one or more:
- **Free**
- **One-time purchase** (per version family)
- **Subscription** (monthly/annual access)
- **Usage-based** (per invocation, per compute unit)

For v0.1, start with **Free + One-time** (simpler accounting).

## Revenue Split (Suggested Defaults)
- Creator: 85%
- Platform: 15% (covers review infra, hosting, dispute handling)

Optional:
- reviewer rewards pool funded from platform fee

## Review & Approval Workflow

### Submission
1) Creator mints Artifact to registry → obtains CID
2) Creator submits Listing referencing CID + manifest signature
3) Automated checks run (schema, trust, permissions)

### Review states
- `submitted` → `automated_checked` → `human_reviewed` → `approved` → `published`
- `rejected` with reason; can resubmit new version

### Production install rules
- `verified/core`: auto-install allowed by policy
- `community`: requires user/admin approval + capability grants
- `unsigned`: blocked

## Refunds & Disputes
For paid items:
- default refund window (e.g., 48h) if unused or materially defective
- disputes route to witness/admin

## Discovery & Ranking (Simple v0.1)
Ranking score (suggested inputs):
- trust level (verified > community)
- install count (log-scale)
- rating average (with minimum count)
- refund rate penalty
- recency boost

## Anti-Abuse
- enforce namespace rules for listings (prevent impersonation)
- verified badges for known creators
- rate limits on submissions and updates
- signature validation required for all non-core plugins

## Implementation Notes
- Store marketplace listings as metadata in a small DB (SQLite in v0.1)
- Listings reference Artifact CID; payloads remain immutable via registry
- Economy service handles payments (payouts to creators) with witness gates for large transfers

