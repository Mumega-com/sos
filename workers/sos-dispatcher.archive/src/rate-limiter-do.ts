// Durable Object for per-tenant rate limiting.
//
// One DO instance per tenant. Each instance holds a minute-window counter
// in-memory (and a SQLite row for persistence/Facets-ready).

import type { PLAN_LIMITS_RPM } from "./types.js";

interface RateLimitResult {
  allowed: boolean;
  remaining: number;
  retry_after_s: number;
}

interface RateLimitRequest {
  plan: keyof typeof PLAN_LIMITS_RPM | "starter";
  limits: Record<string, number>;
}

export class RateLimiterDO {
  private windowStart = 0;
  private count = 0;

  constructor(
    private state: DurableObjectState,
    private env: unknown,
  ) {}

  async fetch(request: Request): Promise<Response> {
    const { plan, limits } = (await request.json()) as RateLimitRequest;

    const nowSec = Math.floor(Date.now() / 1000);
    const thisMinute = Math.floor(nowSec / 60);

    if (this.windowStart !== thisMinute) {
      this.windowStart = thisMinute;
      this.count = 0;
    }

    this.count += 1;
    const limit = limits[plan] ?? limits.starter ?? 10;
    const allowed = this.count <= limit;

    const result: RateLimitResult = {
      allowed,
      remaining: Math.max(0, limit - this.count),
      retry_after_s: allowed ? 0 : 60 - (nowSec % 60),
    };

    return Response.json(result);
  }
}
