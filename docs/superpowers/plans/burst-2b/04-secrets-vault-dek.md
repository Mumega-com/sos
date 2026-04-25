# Burst 2B-4 — Secrets Vault + Per-Workspace DEK Envelope Encryption

**Author:** Loom
**Date:** 2026-04-24
**Phase:** Sprint 002 — Burst 2B hardening (zero plaintext secrets)
**Depends on:** Section 1 (workspace isolation), Burst 2B-2 (audit for secret reads)
**Gate:** Athena
**Owner:** Kasra
**Effort:** ~13 days

---

## 1. Goal

Eliminate plaintext secrets on disk and encrypt per-workspace data with per-workspace keys.

- **Secrets manager.** HashiCorp Vault (dev mode for sprint; production-grade cluster in follow-up). Every service reads secrets via Vault's API — nothing reads from `~/.sos/.env`, `tokens.json`, `wrangler.toml`, or any filesystem secret file in steady state.
- **Envelope encryption.** Per-workspace Data Encryption Key (DEK) encrypts that workspace's sensitive columns (engram bodies, OAuth tokens, adapter credentials). The Key Encryption Key (KEK) lives in Vault's transit engine; the DEK lives, wrapped, in the workspace metadata row. Only Vault can unwrap it.

This is the baseline any ISO 42001 / SOC 2 auditor expects and it is the precondition for any enterprise customer procurement.

## 2. Schema

```
workspace_keys (
  workspace_id          TEXT PRIMARY KEY,
  dek_encrypted_with_kek BYTEA NOT NULL,
  kek_ref               TEXT NOT NULL,   -- Vault transit key name + version
  algorithm             TEXT NOT NULL,   -- 'AES-256-GCM'
  created_at            TIMESTAMPTZ NOT NULL,
  rotated_at            TIMESTAMPTZ
)
```

One row per workspace. DEK is generated at workspace creation, wrapped by KEK via Vault transit, and stored. On every sensitive read/write the service asks Vault to unwrap — the unwrapped DEK is held in memory for the request lifetime only.

## 3. Migration Plan

A one-time migration owned by Kasra:

1. **Enumerate.** Script `tools/audit-plaintext-secrets.py` walks the repo + `~/.sos/` + `SOS/sos/bus/tokens.json` + every `wrangler.toml` and emits a CSV of every plaintext secret: path, key name, length, last-modified.
2. **Rotate on move.** Each secret is rotated (new value generated where possible, otherwise re-issued from the provider) and the new value is written directly to Vault — the old value never leaves the rotation step.
3. **Cut over.** Services are updated one at a time to read from Vault. A service-ready check fails the service start if it still reads a known plaintext path.
4. **Delete originals.** After verified cutover, plaintext files are deleted and absence is asserted by a CI check.

## 4. Integration

- **Bindings.** Every worker and service receives a `VAULT_ADDR` + short-lived `VAULT_TOKEN` (AppRole in production; dev token in sprint). Tokens are issued with the minimum policy needed — read-only on their own path prefix.
- **Adapters (§8).** Datalake OAuth tokens and API keys move to Vault. Adapter plugin manifest declares `secrets: [list]`; the loader mounts just those paths.
- **DISP-001.** Session signing keys move to Vault transit (`sign` / `verify` without exposing the key).
- **Audit.** Every Vault read emits a Burst 2B-2 `audit_events` row with the caller identity, key path, and purpose.

## 5. Acceptance Criteria

1. **Zero plaintext on disk.** `tools/audit-plaintext-secrets.py` run against the running system returns zero findings. CI fails on reintroduction.
2. **Every service reads via Vault.** Each service logs `secret_source=vault` on startup for every secret consumed; grep for filesystem secret reads returns none.
3. **Per-workspace DEK roundtrip.** Creating a new workspace provisions a DEK; writing a sensitive column encrypts under that DEK; reading decrypts correctly; a different workspace's DEK cannot decrypt (negative test passes).
4. **KEK rotation.** Rotating the Vault KEK re-wraps all DEKs without downtime; old wrapped DEKs become unreadable after rotation window.
5. **Audit coverage.** Every secret read and every encrypt/decrypt operation produces a Burst 2B-2 audit event; chain verification passes.
