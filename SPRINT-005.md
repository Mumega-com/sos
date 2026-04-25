# Sprint 005 — "Audit-Clean Substrate"

**Sprint window:** 2026-04-26 → 2026-05-09 (~2 weeks calendar; expected ~2-3 bus sessions at current velocity)
**Sprint goal:** From *substrate that routes work to itself* (Sprint 004) to *substrate whose claims about itself are enforceable end-to-end*. Close all 20 adversarial findings (7 P0 + 13 WARN/Low) + the 4 deferred ops items + the 3 SCIM soft notes + the dimension-taxonomy reshape. After this sprint, every claim on the Trust Center page is mathematically defensible.
**Sprint owner:** Loom (coordination + spec) + Kasra (execution lead) + Athena (gate + Mirror)
**Mandate from Hadi:** *Make the substrate audit-clean before shipping customer-readiness in Sprint 006.*
**Status:** v0.1 OPEN — scope (b) MEDIUM picked from three-option menu post-cleanup.

---

## Why scope (b)

Scope picked over (a) NARROW and (c) BROAD per the Sprint 005 scope-decision conversation:

- (a) leaves 13 honest WARN/Low findings open. Trust Center claims would carry footnotes.
- (c) mixes substrate work (audit closure) with calendar-bound external items (SOC 2 Type II requires 12 months operation; ISO cert cycles). Better separated.
- (b) closes ALL adversarial findings + ops items + the dimension reshape. Substrate becomes audit-clean. Sprint 006 then opens with clean substrate for customer-readiness arc (production HA + SOC 2 + ToRivers + first IdP playbook).

---

## Track A — Constitutional integrity arc closure (~1d)

The 4 deferred P0 ops items + 3 SCIM soft notes + dimension reshape + live-flip gate.

| # | Task | Owner | Effort | Gate |
|---|---|---|---|---|
| A.1 | F-02b: superuser migration to REVOKE INSERT FROM mirror on `reputation_events` | Athena (schema gate) + Kasra (build) | 0.25d | G19 |
| A.2 | F-01b + P0-2b: superuser migration for `frc_verdicts` REVOKE EXECUTE FROM PUBLIC + GRANT to classifier_role; backfill existing `mirror_engrams.classifier_run_log` rows into `frc_verdicts` (fail-open transition) | Athena + Kasra | 0.5d | G20 |
| A.3 | F-11b: signature enforcement in `audit_to_reputation` once `AUDIT_SIGNING_KEY` distributed (HSM or kernel keystore) | Kasra | 0.25d | G21 |
| A.4 | 3 SCIM soft notes — unknown-tier default, scim_deprovision dead param, add_group_role_map tenant hardening | Kasra | 0.25d | SCIM gate amend |
| A.5 | **G_A4b** A.4 quest_vectors rewrite to `lambda_dna` dimensions; demote work-skills 16-dim taxonomy to §14 Inventory `capability_kind='work_skill'` discrete tags (per Athena retro call) | Kasra (build) + Loom (spec amend if needed) | 1d | G_A4b |
| A.6 | E2E test execution against substrate post-A.1-A.5; three-way sign-off (Athena correctness / Kasra implementation / Loom observability) | All three | 0.25d | live-flip auth |
| A.7 | matchmaker.service flip DRY_RUN → live (single env var change) | Kasra | 5min | none |

**Acceptance:** all 7 P0 BLOCK fixes complete with REVOKE actually enforcing. `frc_emit_verdict()` callable only by classifier_role. Audit chain trigger validates Ed25519 signature. SCIM cross-tenant + tier-ceiling escalation paths closed. Quest vectors and citizen vectors share same coordinate basis. matchmaker.service live in production.

---

## Track B — 13 WARN/Low findings (~3-5d)

All adversarial findings closed at code level. Substrate becomes audit-clean.

| # | Finding | Fix shape | Owner | Gate |
|---|---|---|---|---|
| B.1 | F-03 stake-weighted σ shrinkage | Multiply event contribution by tier weight in `_glicko2_update`: T1=0.25, T2=0.5, T3=1.0, T4=1.5; add `quest_tier` to `reputation_events` | Kasra | G22 |
| B.2 | F-04 record_assignment race | `pg_advisory_xact_lock(hashtext(quest_id))` before computing offer_count | Kasra | G23 |
| B.3 | F-06 cold-start vector seed (direction leak) | Replace `0.5 × quest_v` seed with uniform `[0.5]*16`; require N≥3 distinct accepted before persisting citizen vector | Kasra | G24 |
| B.4 | F-07 `_wrap_dek` AAD = None | Pass `aad = workspace_id.encode() + b'\|' + str(kek_version).encode()` in both wrap and unwrap | Kasra | G25 |
| B.5 | F-08 Vault token cache global | Per-workspace cache; tag entries with request id; 403 evicts only the affected entry | Kasra | G26 |
| B.6 | F-09 TOTP replay ledger | New `mfa_used_codes(principal_id, code_hash, used_at)` table with PK on (principal_id, code_hash); INSERT before returning True; reject on conflict | Kasra | G27 |
| B.7 | F-12 coherence_check_v1 newest-only | `min(verdict_score for v in verdicts within window)` so any failed verdict in lookback sticks | Kasra | G28 |
| B.8 | F-13 quest description unbounded | `CHECK (length(description) <= 4096)` on quests; truncate to 2048 in `_build_prompt`; per-creator extraction quota 10/day | Kasra | G29 |
| B.9 | F-14 process_outcomes synchronous recompute | Mark candidates dirty in small `reputation_dirty_holders` table; recompute once per tick per holder; cap batch_size; tick-budget abort | Kasra | G30 |
| B.10 | F-16 σ leakage UCB/LCB | API surfaces return only rank ordinals not composite_score floats; or quantize composite_score to 0.05 buckets | Kasra | G31 |
| B.11 | F-18 `recompute()` no auth gate | Add `caller_id: str` parameter; verify principal has role `kernel_admin` via `principals.has_role` | Kasra | G32 |
| B.12 | F-19 quests.created_by no FK | `ALTER TABLE quests ADD CONSTRAINT fk_quests_created_by FOREIGN KEY (created_by) REFERENCES principals(id)`; validate at API | Kasra | G33 |
| B.13 | F-20 SAML assertion replay ledger | New `saml_used_assertions(assertion_id PK, used_at)` table with TTL cleanup; reject on conflict | Kasra | G34 |

**Acceptance:** every adversarial finding from the 2026-04-25 review has a closed code path. Substrate's Trust Center page claims become enforceable end-to-end without footnotes.

---

## Track C — Sprint observability (~0.5d)

Make sprint metrics measurable from kernel emissions (not freehand counts in retro docs).

| # | Task | Owner | Effort |
|---|---|---|---|
| C.1 | Emit `gate_verdict` audit_events action — payload `{gate_id, verdict, ts}` — wired into Athena's bus-send pattern (auto-parses `^G\d+[a-z]?\s+(GREEN\|YELLOW\|BLOCKED\|RESHAPE)`) | Athena offered to wire + Loom drafts parser | 0.25d |
| C.2 | Emit `incident_resolved` audit_events action — payload `{incident_id, severity, root_cause}` — Athena auto-emits when she sends overnight save messages | Athena | 0.25d |
| C.3 | Emit `adversarial_finding` audit_events action — payload `{finding_id, severity, category, fix_status}` — Loom emits per finding when adversarial subagent runs | Loom | 0.25d |
| C.4 | Health alerting on systemd unit `RestartCount > 5` triggers bus message to athena | Kasra | 0.25d |
| C.5 | `/sprint-stats` skill — generates close-out report from sprint markers + audit_events roll-up | Loom | 0.25d |

**Acceptance:** Sprint 005 close-out runs `python -m sos.observability.sprint_telemetry stats sprint-005` and produces real measured counts (not freehand). Sprint 006 retro can cite measured data.

---

## Track D — Architectural protocol (parallel, low-effort)

Codify the lessons learned from Sprint 003+004 retro.

| # | Task | Owner | Effort |
|---|---|---|---|
| D.1 | Codify adversarial-as-parallel-gate in CLAUDE.md + `~/.claude/rules/agent-comms.md` | Loom | 0.25d |
| D.2 | Update brief templates with literal-verb trigger order (`drafts → triggers → gates → builds`); store template at `agents/loom/briefs/_template.md` | Loom | 0.25d |
| D.3 | Sprint 003+004 retro: Kasra read sign-off (Athena already signed) | Kasra | 5min |
| D.4 | Mirror /Archive cleanup (X.9 carry from Sprint 003) — Project_Chimera, Scratchpad alphas inflating graph noise | Loom | 0.5d |

---

## Strategic carries (Hadi's lane, dropped from Loom status updates per 2026-04-25 directive)

Tracked here for sprint-completeness only; Loom will not surface in status updates.

- Vertex `text-embedding-004` quota request OR Gemini key renewal (Mirror on local-onnx fallback works in meantime)
- Mumega Inc. Stripe Atlas + 83(b) within 30 days of stock issuance
- Ron Tuesday 2 PM (2026-04-28) — first customer follow-up
- YC May 4 pitch finalization
- USPTO MUMEGA wordmark §1(b) intent-to-use

---

## Definition of done

- **Track A:** matchmaker.service flipped DRY_RUN → live with three-way sign-off; all 7 P0 BLOCKs structurally enforced (REVOKE actually enforcing, signature actually validating, tier ceiling actually capping)
- **Track B:** every WARN/Low adversarial finding closed at code level; Trust Center claims footnote-free
- **Track C:** sprint observability emits real measured counts; Sprint 006 retro auto-generates from data
- **Track D:** adversarial-as-parallel-gate canonical in agent-comms standard; brief templates updated

After Sprint 005 close: substrate is audit-clean. Sprint 006 opens with focused customer-readiness arc (production HA + SOC 2 prep + first customer IdP playbook + CSA STAR filing + ToRivers groundwork) without inheriting any constitutional integrity debt.

---

## Gate scoreboard projection

| Gate | What | Owner | Trigger |
|---|---|---|---|
| G19 | F-02b superuser REVOKE | Athena | Kasra drafts |
| G20 | F-01b + P0-2b frc_verdicts ownership | Athena | Kasra drafts |
| G21 | F-11b signature enforcement | Athena | Kasra ships |
| G_A4b | Quest_vectors lambda_dna rewrite | Athena | Kasra ships A.5 |
| G22-G34 | Each WARN/Low fix gates individually | Athena | Kasra ships per item |
| Live-flip | Three-way sign-off post-E2E | Athena correctness + Kasra implementation + Loom observability | All Track A complete |

13 WARN/Low gates can batch into 3-4 grouped reviews if Athena prefers.

---

## Versioning

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-04-25 | Initial open, scope (b) MEDIUM. 4 P0 deferred ops + 3 SCIM soft notes + 13 WARN/Low + dimension reshape + sprint observability + protocol codification + Mirror /Archive cleanup. |
