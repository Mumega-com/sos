# Bug Report Format — reporters please follow

When you send a bus message or open a task to `sos-medic`, include these fields. Missing fields cost round-trips.

## Required

```
SYMPTOM: <one-line what is broken from the user's perspective>
PIPE: <one of: bus | squad | saas | mirror | dashboard | nginx | wake-daemon | MCP-SSE | unknown>
SEVERITY: <blocker | normal | fyi>
REPRO:
  1. <exact curl or MCP call>
  2. <what you expected>
  3. <what you got instead>
WHEN: <iso timestamp of first observation>
```

## Optional but useful

```
SCOPE: <which tenant/project is affected, e.g. "trop" or "all customers" or "admin only">
RELATED: <task id, commit hash, or PR number>
TRIED: <what you already checked — don't make the medic redo your work>
```

## Example

```
SYMPTOM: trop customer can't log in to app.mumega.com/dashboard
PIPE: dashboard
SEVERITY: blocker
REPRO:
  1. curl -X POST https://app.mumega.com/login -d "token=sk-trop-<...>"
  2. expected: 303 redirect to /dashboard
  3. got: 401 Unauthorized
WHEN: 2026-04-16T21:25:00Z
SCOPE: trop + likely all post-SEC-001 customers
TRIED: confirmed token exists in tokens.json with active=true
```

---

# Incident Log Template

The medic writes one of these after every fix. File: `incidents/YYYY-MM-DD-<slug>.md`.

```markdown
# Incident YYYY-MM-DD — <short title>

**Reporter:** <agent name or user>
**Severity:** <blocker | normal | fyi>
**Time to resolve:** <minutes>
**Pipe:** <bus | squad | saas | mirror | dashboard | nginx | wake-daemon | MCP-SSE>
**Medic version:** <x.y.z at time of fix>

## Symptom
<one paragraph, reporter's view>

## Reproduction
<exact commands the medic ran, with outputs>

## Root cause
<file:line + one sentence + link to offending commit if known>

## Fix
<diff summary + commands run>

## Verification
<commands that now pass, with outputs>

## Pattern class
<for experience log — is this a recurrent class of bug? e.g. "SEC-001 auth regression", "nginx redirect instead of proxy", "systemd unit not restarted after config change">

## Followups
<any upstream fixes needed, tickets opened, handoffs made>
```
