// SOS Dispatcher — Cloudflare Worker implementation.
//
// Protocol: docs/plans/2026-04-17-dispatcher-protocol.md
// Same contract as sos/services/dispatcher/ (Python impl).

import { Hono } from "hono";

import { identityHeaders, resolveToken } from "./auth.js";
import { checkRateLimit } from "./rate-limit.js";
import { logRequest } from "./request-log.js";
import type { AuthContext, Env, SosError } from "./types.js";

export { RateLimiterDO } from "./rate-limiter-do.js";

const app = new Hono<{ Bindings: Env }>();

// ── Error helpers ────────────────────────────────────────────────────────────

function errorResponse(status: number, err: SosError): Response {
  const headers: HeadersInit = { "Content-Type": "application/json" };
  if (err.code === "SOS-9003" && err.retry_after) {
    headers["Retry-After"] = String(err.retry_after);
  }
  return new Response(JSON.stringify(err), { status, headers });
}

// ── Gate: auth + rate limit ──────────────────────────────────────────────────

async function gate(
  env: Env,
  token: string,
): Promise<{ ctx: AuthContext } | { error: Response }> {
  const ctx = await resolveToken(env, token);
  if (!ctx) {
    return { error: errorResponse(401, { code: "SOS-1001", message: "invalid token" }) };
  }

  const rl = await checkRateLimit(env, ctx);
  if (!rl.allowed) {
    return {
      error: errorResponse(429, {
        code: "SOS-9003",
        message: "rate limit exceeded",
        retry_after: rl.retry_after_s,
      }),
    };
  }

  return { ctx };
}

// ── Upstream URL helper ──────────────────────────────────────────────────────

function upstreamUrl(env: Env, path: string, query?: string): string {
  const base = `http://${env.UPSTREAM_HOST}:${env.UPSTREAM_PORT}`;
  return query ? `${base}${path}?${query}` : `${base}${path}`;
}

// ── Handlers ─────────────────────────────────────────────────────────────────

app.get("/health", (c) => {
  return c.json({
    status: "ok",
    service: "sos-dispatcher",
    source: c.env.DISPATCHER_SOURCE,
    upstream: `${c.env.UPSTREAM_HOST}:${c.env.UPSTREAM_PORT}`,
  });
});

app.get("/sse/:token", async (c) => {
  const start = Date.now();
  const token = c.req.param("token");
  const gated = await gate(c.env, token);

  if ("error" in gated) {
    logRequest(c.env, { waitUntil: c.executionCtx.waitUntil.bind(c.executionCtx) }, {
      tenant_id: null, agent: "unknown", scope: "unknown",
      endpoint: "/sse", method: "GET",
      status: gated.error.status, latency_ms: Date.now() - start, bytes_out: 0,
      error_code: gated.error.status === 401 ? "SOS-1001" : "SOS-9003",
    });
    return gated.error;
  }

  const headers = new Headers(c.req.raw.headers);
  headers.delete("host");
  for (const [k, v] of Object.entries(identityHeaders(gated.ctx, c.env.DISPATCHER_SOURCE))) {
    headers.set(k, v);
  }

  const upstream = await fetch(upstreamUrl(c.env, `/sse/${token}`), {
    method: "GET",
    headers,
  });

  logRequest(c.env, { waitUntil: c.executionCtx.waitUntil.bind(c.executionCtx) }, {
    tenant_id: gated.ctx.tenant_id, agent: gated.ctx.agent, scope: gated.ctx.scope,
    endpoint: "/sse", method: "GET",
    status: upstream.status, latency_ms: Date.now() - start, bytes_out: 0,
    error_code: null,
  });

  return new Response(upstream.body, {
    status: upstream.status,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-store",
      "X-Accel-Buffering": "no",
      "Connection": "keep-alive",
    },
  });
});

app.post("/messages", async (c) => {
  const start = Date.now();
  const body = await c.req.raw.arrayBuffer();
  const headers = new Headers(c.req.raw.headers);
  headers.delete("host");
  headers.set("X-SOS-Source", c.env.DISPATCHER_SOURCE);

  const query = c.req.url.split("?")[1] || "";
  const upstream = await fetch(upstreamUrl(c.env, "/messages", query), {
    method: "POST",
    headers,
    body,
  });

  const responseBody = await upstream.arrayBuffer();

  logRequest(c.env, { waitUntil: c.executionCtx.waitUntil.bind(c.executionCtx) }, {
    tenant_id: null, agent: "session", scope: "session",
    endpoint: "/messages", method: "POST",
    status: upstream.status, latency_ms: Date.now() - start,
    bytes_out: responseBody.byteLength,
    error_code: null,
  });

  return new Response(responseBody, {
    status: upstream.status,
    headers: upstream.headers,
  });
});

app.post("/mcp/:token", async (c) => {
  const start = Date.now();
  const token = c.req.param("token");
  const gated = await gate(c.env, token);

  if ("error" in gated) {
    logRequest(c.env, { waitUntil: c.executionCtx.waitUntil.bind(c.executionCtx) }, {
      tenant_id: null, agent: "unknown", scope: "unknown",
      endpoint: "/mcp", method: "POST",
      status: gated.error.status, latency_ms: Date.now() - start, bytes_out: 0,
      error_code: gated.error.status === 401 ? "SOS-1001" : "SOS-9003",
    });
    return gated.error;
  }

  const body = await c.req.raw.arrayBuffer();
  const headers = new Headers(c.req.raw.headers);
  headers.delete("host");
  for (const [k, v] of Object.entries(identityHeaders(gated.ctx, c.env.DISPATCHER_SOURCE))) {
    headers.set(k, v);
  }

  const upstream = await fetch(upstreamUrl(c.env, `/mcp/${token}`), {
    method: "POST",
    headers,
    body,
  });

  const responseBody = await upstream.arrayBuffer();

  logRequest(c.env, { waitUntil: c.executionCtx.waitUntil.bind(c.executionCtx) }, {
    tenant_id: gated.ctx.tenant_id, agent: gated.ctx.agent, scope: gated.ctx.scope,
    endpoint: "/mcp", method: "POST",
    status: upstream.status, latency_ms: Date.now() - start,
    bytes_out: responseBody.byteLength,
    error_code: null,
  });

  return new Response(responseBody, {
    status: upstream.status,
    headers: upstream.headers,
  });
});

// 404 fallback
app.notFound((c) =>
  c.json({ code: "SOS-9999", message: "not found" }, 404)
);

// Error handler
app.onError((err, c) => {
  console.error("dispatcher error:", err);
  return c.json({ code: "SOS-9998", message: "internal error" }, 500);
});

export default {
  fetch: app.fetch,
};
