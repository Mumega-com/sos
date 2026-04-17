// Shared types for the SOS Dispatcher CF Worker.
//
// Protocol: docs/plans/2026-04-17-dispatcher-protocol.md

export interface Env {
  // KV: sha256(token) → JSON-stringified AuthContext
  SOS_TOKENS: KVNamespace;

  // D1: request log
  DISPATCHER_LOG: D1Database;

  // DO: per-tenant rate limiter
  RATE_LIMITER: DurableObjectNamespace;

  // Vars from wrangler.toml
  UPSTREAM_HOST: string;
  UPSTREAM_PORT: string;
  DISPATCHER_SOURCE: string;
}

export interface AuthContext {
  tenant_id: string | null;
  agent: string;
  scope: "agent" | "customer" | "admin";
  plan: "starter" | "growth" | "scale" | "enterprise" | null;
  role: "admin" | "operator" | "viewer";
}

export interface SosError {
  code: string;
  message: string;
  retry_after?: number;
}

export const PLAN_LIMITS_RPM: Record<string, number> = {
  starter: 10,
  growth: 100,
  scale: 1000,
  enterprise: 10_000,
};
