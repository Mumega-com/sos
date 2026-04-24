# Security Policy

**Version:** v1.0 (2026-04-24)

## Reporting a Vulnerability

We take security seriously. If you've discovered a security vulnerability in any Mumega / SOS / Mirror / Squad Service / Inkwell component, **please do not file a public issue.**

**Report to:** security@mumega.com (or hadi@digid.ca until DNS is updated)

**Encrypt sensitive details with our PGP key:** *(key publication pending — for now, email a request and we'll exchange a public key out-of-band)*

## What to include

- Component affected (SOS kernel, Mirror, Squad, Inkwell, MCP dispatcher, plugin, etc.)
- Steps to reproduce
- Impact assessment (data exposure, privilege escalation, DoS, etc.)
- Suggested remediation (if you have one)
- Whether you'd like public credit on disclosure

## Our commitment

- Acknowledge receipt within **48 hours**
- Initial triage within **5 business days**
- Status update at minimum every **2 weeks** until resolved
- Coordinated public disclosure once a fix is deployed
- Credit given (with your consent) in the security changelog

## Scope

**In scope:**
- Code in this repository and adjacent Mumega-owned repositories
- Production services at `*.mumega.com`, `mcp.mumega.com`, `api.mumega.com`, `app.mumega.com`
- Customer products built on the substrate (GAF at grantandfunding.com, etc.)
- MCP plugins published by Mumega

**Out of scope:**
- Third-party services we depend on (Cloudflare, Supabase, Anthropic, etc.) — report to them directly
- Social engineering of Mumega team members
- DoS attacks against shared infrastructure
- Issues in customer-deployed forks of Inkwell that we don't operate

## Safe harbor

We will not pursue legal action against researchers who:
- Make a good-faith effort to comply with this policy
- Avoid privacy violations, destruction of data, or disruption of service
- Give us reasonable time to address the issue before public disclosure
- Do not exploit a vulnerability beyond what's necessary to demonstrate it

## Bug bounty

Currently informal — recognition + Mumega swag for valid reports. Formal bounty program will be announced after SOC2 Type I.

## Hall of fame

*(Researchers who have responsibly disclosed will be listed here with their consent.)*

---

## Versioning

| Version | Date | Change |
|---|---|---|
| v1.0 | 2026-04-24 | Initial security policy. |
