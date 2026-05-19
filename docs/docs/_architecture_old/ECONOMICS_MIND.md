# SOS Economy — $MIND Tokenomics (Draft)

Status: Draft v0.1  
Owner: Codex  
Last Updated: 2026-01-10

## Purpose
Define a simple, implementable economic model for `$MIND` that can be enforced by the SOS Economy Service.

This document is a *policy layer* (rules + defaults). It should map cleanly to:
- `sos/contracts/economy.py` transaction types and statuses
- Edition policies (River) and engine/economy enforcement

## Definitions

### Units
- **Currency:** `MIND`
- **Accounting unit:** `microMIND` (integer). `1 MIND = 1_000_000 microMIND`.
- In code: store amounts as `int` `microMIND` (`Transaction.amount`).

### Actors
- **Treasury:** `treasury` is the default issuer and counterparty for system payouts/fees.
- **Agent:** earns/spends MIND within edition constraints.
- **Witness:** approves high-risk transactions (payouts above threshold, all slashing).
- **User:** optionally funds Treasury (off-chain or on-chain) and sets budgets/limits.

## Supply Model (Local-First)

### v0.1: Treasury-Minted Credits (Budget-Backed)
MIND is treated as an internal credit system:
- Supply increases only via **explicit treasury mint events** (funding, admin issuance).
- Supply decreases via **burn events** (optional) or remains circulating (fees accumulate to treasury).

**Rationale:** avoids premature on-chain complexity while still enabling enforcement, accounting, and incentives.

### On-Chain Bridge (Optional)
When enabled, Treasury can support:
- **Deposit:** user deposits SOL/USDC → treasury mints equivalent MIND credits.
- **Withdraw:** treasury burns MIND credits → treasury sends SOL/USDC (manual at first).

*Rate is treasury-defined and can be changed via policy + witness.*

## Earning Mechanisms

### 1) Task Completion Payouts (Primary)
When a task reaches `completed` (and passes any dispute window rules), the Economy Service creates a `payout` transaction.

### 2) Quality Bonuses (Optional)
If a task receives a quality score (review/auditor), apply a bounded multiplier.

Suggested:
- `quality_multiplier = clamp(0.8, 1.25, 0.8 + score)` where score ∈ [0, 0.5]

### 3) Bounties / Rewards (Optional)
Treasury may issue rewards for:
- bug fixes, incident response, security patches
- high-value artifacts/plugins (marketplace incentives)

## Spending Sinks

### 1) Tool Usage Fees
Tool calls can charge MIND, especially for:
- outbound network tools
- premium model usage
- heavy sandbox execution

### 2) Storage & Artifact Minting Fees
Artifact Registry can charge:
- per artifact minted
- per MB stored/month (or per bundle size)

### 3) Marketplace Purchases
Buying plugins/artifacts transfers MIND to creators (minus platform fee).

## Pricing Framework (Tasks)

### Task Classes
Define task “complexity classes” (maps to pricing defaults; human can override):
- **S (Small):** 5–30 min
- **M (Medium):** 0.5–2 h
- **L (Large):** 2–8 h
- **XL (Risky):** 8h+ or security-critical

### Baseline Rates (microMIND)
Pick a base rate per class (example placeholders; tune later):
- S: 5–25 MIND
- M: 25–150 MIND
- L: 150–800 MIND
- XL: 800–5000 MIND (usually witness-gated)

## Payout Calculation (Suggested v0.1)

Let:
- `base` = class baseline or explicit task price
- `q` = quality multiplier (default 1.0)
- `r` = risk multiplier (default 1.0; set >1 for security/production changes)

Then:
- `payout = round(base * q * r)`

**Holdbacks:**
- If a dispute window exists, optionally hold back `h%` until window closes.

## Slashing Policy (v0.1)
Slashing exists to discourage harmful behavior. All slashing requires witness approval.

### Violation Types (examples)
- **Policy violation:** disallowed content/tool usage
- **Security violation:** secret exfiltration, sandbox escape attempt
- **Fraud:** falsified proof/artifact mismatch
- **Low-quality abuse:** repeated low-effort submissions

### Suggested Slash Amounts (starting point)
- Low-quality abuse: 5–25% of last payout (cap at 200 MIND)
- Policy violation: fixed 50–500 MIND depending on severity
- Security/fraud: 100% of related payout + potential suspension/termination (non-economic action)

## Witness Thresholds
Suggested default gates (policy-configurable):
- Any `slash` → witness required
- Any `payout` ≥ 150 MIND → witness required
- Any transaction touching external chain (`SOL`, `USDC`) → witness required

## Treasury Risk Controls
- Daily payout limit per agent (edition-dependent)
- Global daily payout limit
- Per-tool rate limits + spend caps
- Circuit breaker: if wallet adapter errors spike, disable on-chain settlement

## Implementation Notes (Mapping to Contracts)
- Use `TransactionStatus` state machine from `STATE_MACHINES.md`.
- Encode MIND as integer micro-units (`Transaction.amount`).
- When Artifact Registry exists: store `artifact_cid` and `task_id` in `Transaction.metadata`.

## Open Parameters (Need Calibration)
- base class pricing table
- witness threshold values
- platform fees (marketplace)
- conversion rate to SOL/USDC (if enabled)

