// Rate-limit helper — calls the Durable Object for the tenant.

import type { AuthContext, Env } from "./types.js";
import { PLAN_LIMITS_RPM } from "./types.js";

export async function checkRateLimit(
  env: Env,
  ctx: AuthContext,
): Promise<{ allowed: boolean; remaining: number; retry_after_s: number }> {
  if (!ctx.tenant_id) {
    // Admin/internal agents skip rate-limit
    return { allowed: true, remaining: -1, retry_after_s: 0 };
  }

  const id = env.RATE_LIMITER.idFromName(ctx.tenant_id);
  const stub = env.RATE_LIMITER.get(id);

  const resp = await stub.fetch("https://rate-limiter/check", {
    method: "POST",
    body: JSON.stringify({ plan: ctx.plan || "starter", limits: PLAN_LIMITS_RPM }),
  });

  return resp.json();
}
