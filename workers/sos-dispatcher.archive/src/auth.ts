// Token validation against KV.
//
// Token storage: KV key `token:<sha256(raw)>` → JSON-stringified AuthContext.
// Populated by scripts/sync-tokens-to-kv.py on every tokens.json change.

import type { AuthContext, Env } from "./types.js";

export async function sha256Hex(input: string): Promise<string> {
  const enc = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", enc);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

export async function resolveToken(env: Env, token: string): Promise<AuthContext | null> {
  if (!token) return null;
  const hash = await sha256Hex(token);
  const raw = await env.SOS_TOKENS.get(`token:${hash}`);
  if (!raw) return null;
  try {
    const ctx = JSON.parse(raw) as AuthContext;
    // Basic shape guard
    if (!ctx.agent || !ctx.scope) return null;
    return ctx;
  } catch {
    return null;
  }
}

export function identityHeaders(ctx: AuthContext, source: string): Record<string, string> {
  return {
    "X-SOS-Identity": `agent:${ctx.agent}`,
    "X-SOS-Tenant-Id": ctx.tenant_id || "",
    "X-SOS-Scope": ctx.scope,
    "X-SOS-Plan": ctx.plan || "",
    "X-SOS-Role": ctx.role,
    "X-SOS-Source": source,
  };
}
