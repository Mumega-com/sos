/**
 * SOS Bus Worker — Pure Cloudflare, zero external dependencies.
 * D1 for messages + registry. KV for tokens. R2 for SDK.
 *
 * Scales to 100k+ users on Cloudflare free/paid tier.
 */

export interface Env {
  DB: D1Database
  KV: KVNamespace
  SDK: R2Bucket
  ORIGIN_URL: string
}

// --- Helpers ---

function json(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' },
  })
}

function uuid(): string { return crypto.randomUUID() }
function now(): string { return new Date().toISOString() }

// --- Auth ---

interface Token { project: string | null; label: string; active: boolean }

async function auth(env: Env, req: Request): Promise<Token | null> {
  const header = req.headers.get('Authorization') || ''
  if (!header.startsWith('Bearer ')) return null
  const raw = header.slice(7)
  const cached = await env.KV.get(`token:${raw}`, 'json') as Token | null
  if (cached && cached.active) return cached
  // Fallback: check D1
  const row = await env.DB.prepare('SELECT project, label, active FROM tokens WHERE token = ?').bind(raw).first()
  if (!row || !row.active) return null
  const token: Token = { project: row.project as string | null, label: row.label as string, active: true }
  await env.KV.put(`token:${raw}`, JSON.stringify(token), { expirationTtl: 3600 })
  return token
}

// --- D1 Schema Bootstrap ---

async function ensureSchema(db: D1Database) {
  await db.batch([
    db.prepare(`CREATE TABLE IF NOT EXISTS messages (
      id TEXT PRIMARY KEY,
      stream TEXT NOT NULL,
      source TEXT,
      target TEXT,
      type TEXT DEFAULT 'chat',
      payload TEXT,
      project TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )`),
    db.prepare(`CREATE INDEX IF NOT EXISTS idx_messages_stream ON messages(stream, created_at DESC)`),
    db.prepare(`CREATE TABLE IF NOT EXISTS registry (
      agent TEXT NOT NULL,
      project TEXT DEFAULT '',
      tool TEXT,
      summary TEXT,
      last_seen TEXT,
      PRIMARY KEY (agent, project)
    )`),
    db.prepare(`CREATE TABLE IF NOT EXISTS tokens (
      token TEXT PRIMARY KEY,
      project TEXT,
      label TEXT,
      active INTEGER DEFAULT 1,
      created_at TEXT DEFAULT (datetime('now'))
    )`),
  ])
}

// --- Handlers ---

async function handleHealth(env: Env): Promise<Response> {
  try {
    await env.DB.prepare('SELECT 1').first()
    return json({ status: 'ok', backend: 'cloudflare-d1', edge: true })
  } catch (e) {
    return json({ status: 'error', detail: String(e) }, 500)
  }
}

async function handleSDK(env: Env): Promise<Response> {
  const obj = await env.SDK.get('remote.js')
  if (!obj) return json({ error: 'SDK not found' }, 404)
  return new Response(obj.body, {
    headers: {
      'Content-Type': 'application/javascript',
      'Cache-Control': 'public, max-age=3600',
      'Access-Control-Allow-Origin': '*',
    },
  })
}

async function handleSend(env: Env, body: Record<string, string>, project: string | null): Promise<Response> {
  const { from = 'unknown', to, text } = body
  if (!to || !text) return json({ error: "Missing 'to' or 'text'" }, 400)

  const id = uuid()
  const stream = project ? `project:${project}:agent:${to}` : `global:agent:${to}`
  const payload = JSON.stringify({ text })

  await env.DB.prepare(
    'INSERT INTO messages (id, stream, source, target, type, payload, project, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
  ).bind(id, stream, `agent:${from}`, `agent:${to}`, 'chat', payload, project, now()).run()

  return json({ status: 'sent', id, stream, project })
}

async function handleInbox(env: Env, url: URL, project: string | null): Promise<Response> {
  const agent = url.searchParams.get('agent') || 'unknown'
  const limit = parseInt(url.searchParams.get('limit') || '10')
  const stream = project ? `project:${project}:agent:${agent}` : `global:agent:${agent}`

  const rows = await env.DB.prepare(
    'SELECT id, source, type, payload, project, created_at FROM messages WHERE stream = ? ORDER BY created_at DESC LIMIT ?'
  ).bind(stream, limit).all()

  const messages = (rows.results || []).map(row => {
    let text = ''
    try { text = JSON.parse(row.payload as string).text } catch { text = row.payload as string }
    return {
      id: row.id, source: row.source, type: row.type, text,
      timestamp: row.created_at, project: row.project,
    }
  })

  return json({ agent, project, messages })
}

async function handlePeers(env: Env, project: string | null): Promise<Response> {
  const where = project ? 'WHERE project = ?' : ''
  const stmt = project
    ? env.DB.prepare(`SELECT agent, tool, summary, last_seen, project FROM registry ${where}`).bind(project)
    : env.DB.prepare(`SELECT agent, tool, summary, last_seen, project FROM registry`)

  const rows = await stmt.all()
  return json({ project, agents: rows.results || [] })
}

async function handleBroadcast(env: Env, body: Record<string, string>, project: string | null): Promise<Response> {
  const { from = 'unknown', text, squad } = body
  if (!text) return json({ error: "Missing 'text'" }, 400)

  const id = uuid()
  const stream = squad
    ? (project ? `project:${project}:squad:${squad}` : `global:squad:${squad}`)
    : (project ? `project:${project}:broadcast` : `global:broadcast`)

  await env.DB.prepare(
    'INSERT INTO messages (id, stream, source, target, type, payload, project, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)'
  ).bind(id, stream, `agent:${from}`, stream, 'broadcast', JSON.stringify({ text }), project, now()).run()

  return json({ status: 'broadcast', id, stream, project })
}

async function handleAnnounce(env: Env, body: Record<string, string>, project: string | null): Promise<Response> {
  const { agent = 'unknown', tool = 'remote', summary = '' } = body
  await env.DB.prepare(
    `INSERT OR REPLACE INTO registry (agent, project, tool, summary, last_seen) VALUES (?, ?, ?, ?, ?)`
  ).bind(agent, project || '', tool, summary || `${tool} session`, now()).run()

  return json({ status: 'announced', agent, project })
}

async function handleHeartbeat(env: Env, body: Record<string, string>, project: string | null): Promise<Response> {
  const { agent = 'unknown' } = body
  await env.DB.prepare(
    'UPDATE registry SET last_seen = ? WHERE agent = ? AND project = ?'
  ).bind(now(), agent, project || '').run()

  return json({ status: 'ok' })
}

async function handleAsk(env: Env, req: Request, body: Record<string, string>, project: string | null): Promise<Response> {
  // Project-scoped tokens cannot call global agents (security boundary)
  if (project) {
    return json({ error: 'ask requires admin access. Use send for project messaging.' }, 403)
  }
  // Admin tokens proxy to origin (OpenClaw runs on Hetzner)
  const origin = env.ORIGIN_URL || 'https://mumega.com'
  try {
    const res = await fetch(`${origin}:6380/ask`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': req.headers.get('Authorization') || '' },
      body: JSON.stringify(body),
    })
    return new Response(res.body, { status: res.status, headers: { 'Content-Type': 'application/json' } })
  } catch (e) {
    return json({ error: `Origin unreachable: ${e}` }, 502)
  }
}

// --- Main ---

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)
    const path = url.pathname

    // CORS
    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
        'Access-Control-Allow-Headers': 'Authorization, Content-Type',
      }})
    }

    // No-auth endpoints
    if (path === '/health') return handleHealth(env)
    if (path === '/sdk/remote.js') return handleSDK(env)

    // Schema bootstrap (runs once, idempotent)
    await ensureSchema(env.DB)

    // Auth required
    const token = await auth(env, request)
    if (!token) return json({ error: 'Unauthorized' }, 401)
    const project = token.project

    // Route
    if (request.method === 'GET') {
      if (path === '/inbox') return handleInbox(env, url, project)
      if (path === '/peers') return handlePeers(env, project)
    }

    if (request.method === 'POST') {
      const body = await request.json() as Record<string, string>
      if (path === '/send') return handleSend(env, body, project)
      if (path === '/broadcast') return handleBroadcast(env, body, project)
      if (path === '/announce') return handleAnnounce(env, body, project)
      if (path === '/heartbeat') return handleHeartbeat(env, body, project)
      if (path === '/ask') return handleAsk(env, request, body, project)
    }

    return json({ error: 'Not found' }, 404)
  },
}
