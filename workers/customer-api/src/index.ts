/**
 * Customer API Proxy — thin Cloudflare Worker for mumega.com
 *
 * Proxies requests to SOS services on the VPS. No logic, no state.
 * Auth via Supabase JWT forwarded to services.
 * Workers can't crash. SOS IS the backend.
 */

import { Hono } from 'hono'
import { cors } from 'hono/cors'

interface Env {
  VPS_HOST: string
  SQUAD_PORT: string
  DASHBOARD_PORT: string
  MCP_PORT: string
  STRIPE_SECRET_KEY: string
  STRIPE_PRICE_ID: string
  SITE_URL: string
}

const app = new Hono<{ Bindings: Env }>()

app.use('/*', cors({ origin: '*' }))

// Forward auth header from client to VPS
function proxyHeaders(req: Request): HeadersInit {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  const auth = req.headers.get('Authorization')
  if (auth) headers['Authorization'] = auth
  return headers
}

async function proxy(url: string, req: Request, init?: RequestInit): Promise<Response> {
  try {
    const resp = await fetch(url, {
      method: init?.method ?? req.method,
      headers: { ...proxyHeaders(req), ...(init?.headers as Record<string, string> ?? {}) },
      body: init?.body ?? (req.method !== 'GET' ? req.body : undefined),
    })
    const data = await resp.text()
    return new Response(data, {
      status: resp.status,
      headers: { 'Content-Type': resp.headers.get('Content-Type') ?? 'application/json' },
    })
  } catch (err) {
    return new Response(JSON.stringify({ error: 'Service unavailable' }), { status: 502 })
  }
}

// GET /api/status → Dashboard
app.get('/api/status', (c) =>
  proxy(`http://${c.env.VPS_HOST}:${c.env.DASHBOARD_PORT}/api/status`, c.req.raw))

// GET /api/tasks → Squad Service
app.get('/api/tasks', (c) =>
  proxy(`http://${c.env.VPS_HOST}:${c.env.SQUAD_PORT}/tasks`, c.req.raw))

// GET /api/health → MCP SSE health
app.get('/api/health', (c) =>
  proxy(`http://${c.env.VPS_HOST}:${c.env.MCP_PORT}/health`, c.req.raw))

// GET /api/organism → Live organism vitals for homepage
app.get('/api/organism', (c) =>
  proxy(`http://${c.env.VPS_HOST}:${c.env.MCP_PORT}/api/organism`, c.req.raw))

// POST /api/onboard → MCP SSE customer signup
app.post('/api/onboard', (c) =>
  proxy(`http://${c.env.VPS_HOST}:${c.env.MCP_PORT}/api/v1/customers/signup`, c.req.raw))

// POST /api/checkout → Create Stripe checkout session
app.post('/api/checkout', async (c) => {
  const body = await c.req.json<{ slug?: string; email?: string }>()
  const stripe = await import('stripe').then((m) => new m.default(c.env.STRIPE_SECRET_KEY))
  try {
    const session = await stripe.checkout.sessions.create({
      mode: 'subscription',
      line_items: [{ price: c.env.STRIPE_PRICE_ID, quantity: 1 }],
      customer_email: body.email,
      metadata: { slug: body.slug ?? '' },
      success_url: `${c.env.SITE_URL}/welcome?session_id={CHECKOUT_SESSION_ID}`,
      cancel_url: `${c.env.SITE_URL}/pricing`,
    })
    return c.json({ url: session.url })
  } catch (err) {
    return c.json({ error: 'Checkout creation failed' }, 500)
  }
})

// Catch-all
app.all('*', (c) => c.json({ error: 'Not found' }, 404))

export default { fetch: app.fetch }
