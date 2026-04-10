/**
 * SOS Health Check Worker
 *
 * Runs on a Cron Trigger every 5 minutes.
 * Pings all SOS service health endpoints on the VPS.
 * Alerts Discord webhook on failure.
 * Stores last-known status in KV for dashboard queries.
 */

import { Hono } from 'hono'

interface Env {
  KV: KVNamespace
  DISCORD_WEBHOOK_URL: string
  VPS_HOST: string
  HEALTH_TOKEN: string
}

interface ServiceCheck {
  name: string
  url: string
  timeout: number
}

interface CheckResult {
  name: string
  status: 'ok' | 'down' | 'degraded'
  responseTime: number
  statusCode: number | null
  error: string | null
  checkedAt: string
}

const app = new Hono<{ Bindings: Env }>()

function getServices(host: string): ServiceCheck[] {
  return [
    { name: 'mcp-sse', url: `https://mcp.mumega.com/health`, timeout: 10000 },
    { name: 'squad', url: `http://${host}:8060/health`, timeout: 10000 },
    { name: 'bus-bridge', url: `http://${host}:6380/health`, timeout: 10000 },
  ]
}

async function checkService(service: ServiceCheck): Promise<CheckResult> {
  const start = Date.now()
  try {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), service.timeout)

    const resp = await fetch(service.url, {
      signal: controller.signal,
      headers: { 'User-Agent': 'SOS-HealthCheck/1.0' },
    })
    clearTimeout(timeoutId)

    const responseTime = Date.now() - start
    const status = resp.ok ? 'ok' : 'degraded'

    return {
      name: service.name,
      status,
      responseTime,
      statusCode: resp.status,
      error: null,
      checkedAt: new Date().toISOString(),
    }
  } catch (err) {
    return {
      name: service.name,
      status: 'down',
      responseTime: Date.now() - start,
      statusCode: null,
      error: err instanceof Error ? err.message : 'Unknown error',
      checkedAt: new Date().toISOString(),
    }
  }
}

async function alertDiscord(
  webhookUrl: string,
  results: CheckResult[],
  previousResults: CheckResult[] | null,
): Promise<void> {
  const down = results.filter((r) => r.status === 'down')
  const degraded = results.filter((r) => r.status === 'degraded')

  if (down.length === 0 && degraded.length === 0) {
    // Check if we're recovering from a previous outage
    if (previousResults) {
      const prevDown = previousResults.filter((r) => r.status !== 'ok')
      if (prevDown.length > 0) {
        const recovered = prevDown
          .map((p) => results.find((r) => r.name === p.name && r.status === 'ok'))
          .filter(Boolean)

        if (recovered.length > 0) {
          await fetch(webhookUrl, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              content: `**SOS Health Check** — All services recovered.\n${recovered.map((r) => `- ${r!.name}: OK (${r!.responseTime}ms)`).join('\n')}`,
            }),
          })
        }
      }
    }
    return
  }

  const lines: string[] = []

  if (down.length > 0) {
    lines.push(`**DOWN:**`)
    for (const r of down) {
      lines.push(`- ${r.name}: ${r.error ?? 'unreachable'} (${r.responseTime}ms)`)
    }
  }

  if (degraded.length > 0) {
    lines.push(`**DEGRADED:**`)
    for (const r of degraded) {
      lines.push(`- ${r.name}: HTTP ${r.statusCode} (${r.responseTime}ms)`)
    }
  }

  const ok = results.filter((r) => r.status === 'ok')
  if (ok.length > 0) {
    lines.push(`**OK:** ${ok.map((r) => `${r.name} (${r.responseTime}ms)`).join(', ')}`)
  }

  await fetch(webhookUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      content: `**SOS Health Check Alert**\n${lines.join('\n')}`,
    }),
  })
}

async function runHealthCheck(env: Env): Promise<CheckResult[]> {
  const host = env.VPS_HOST || '5.161.216.149'
  const services = getServices(host)
  const results = await Promise.all(services.map(checkService))

  // Get previous results from KV
  const prevRaw = await env.KV.get('health:latest')
  const previousResults: CheckResult[] | null = prevRaw ? JSON.parse(prevRaw) : null

  // Store current results
  await env.KV.put('health:latest', JSON.stringify(results), { expirationTtl: 3600 })

  // Store history entry
  const historyKey = `health:${new Date().toISOString().slice(0, 13)}`
  await env.KV.put(historyKey, JSON.stringify(results), { expirationTtl: 86400 * 7 })

  // Alert if needed
  if (env.DISCORD_WEBHOOK_URL) {
    await alertDiscord(env.DISCORD_WEBHOOK_URL, results, previousResults)
  }

  return results
}

// HTTP endpoint for manual checks / dashboard
app.get('/health', async (c) => {
  const results = await runHealthCheck(c.env)
  const allOk = results.every((r) => r.status === 'ok')
  return c.json({ status: allOk ? 'ok' : 'degraded', services: results }, allOk ? 200 : 503)
})

// Get latest stored results (no new check)
app.get('/health/latest', async (c) => {
  const raw = await c.env.KV.get('health:latest')
  if (!raw) {
    return c.json({ error: 'No health data yet' }, 404)
  }
  return c.json(JSON.parse(raw))
})

export default {
  fetch: app.fetch,

  // Cron trigger: runs every 5 minutes
  async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext): Promise<void> {
    ctx.waitUntil(runHealthCheck(env))
  },
}
