// Async request log writer — writes to D1 without blocking the response.

import type { AuthContext, Env } from "./types.js";

export interface LogEntry {
  ts: string;
  tenant_id: string | null;
  agent: string;
  scope: string;
  endpoint: string;
  method: string;
  status: number;
  latency_ms: number;
  bytes_out: number;
  error_code: string | null;
}

export async function ensureSchema(env: Env): Promise<void> {
  await env.DISPATCHER_LOG.prepare(`
    CREATE TABLE IF NOT EXISTS requests (
      ts TEXT NOT NULL,
      tenant_id TEXT,
      agent TEXT NOT NULL,
      scope TEXT NOT NULL,
      endpoint TEXT NOT NULL,
      method TEXT NOT NULL,
      status INTEGER NOT NULL,
      latency_ms INTEGER NOT NULL,
      bytes_out INTEGER DEFAULT 0,
      error_code TEXT
    )
  `).run();

  await env.DISPATCHER_LOG.prepare(
    "CREATE INDEX IF NOT EXISTS idx_requests_ts ON requests(ts)"
  ).run();

  await env.DISPATCHER_LOG.prepare(
    "CREATE INDEX IF NOT EXISTS idx_requests_tenant ON requests(tenant_id, ts)"
  ).run();
}

export function logRequest(
  env: Env,
  ctx: { ctx?: ExecutionContext; waitUntil?: (p: Promise<unknown>) => void },
  entry: Omit<LogEntry, "ts">,
): void {
  const row: LogEntry = {
    ts: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
    ...entry,
  };

  const p = env.DISPATCHER_LOG.prepare(
    "INSERT INTO requests (ts, tenant_id, agent, scope, endpoint, method, status, latency_ms, bytes_out, error_code) " +
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
  ).bind(
    row.ts,
    row.tenant_id,
    row.agent,
    row.scope,
    row.endpoint,
    row.method,
    row.status,
    row.latency_ms,
    row.bytes_out,
    row.error_code,
  ).run().catch(() => {
    // swallow — logging must never break hot path
  });

  if (ctx.waitUntil) {
    ctx.waitUntil(p);
  }
}
