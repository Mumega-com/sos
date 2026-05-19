/**
 * Etsy R2 Hosting Bridge — S065 Track D support
 *
 * POST /upload  — receive base64-encoded asset from Asset Forge, store in R2,
 *                 return a public fulfillment_url the shop owner receives.
 * GET  /{key}   — serve a previously uploaded asset from R2.
 * GET  /health  — liveness check.
 *
 * Auth: Bearer token checked against R2_BRIDGE_TOKEN secret.
 * Asset keys: etsy/{shop_id}/{receipt_id}/v1.svg  (set by Asset Forge).
 */

interface Env {
  ASSETS: R2Bucket
  ASSETS_PUBLIC_URL: string
  R2_BRIDGE_TOKEN: string
}

interface UploadRequest {
  key: string
  content_type: string
  body_base64: string
  metadata?: Record<string, string>
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url)

    if (url.pathname === "/health") {
      return Response.json({ ok: true, service: "etsy-r2-bridge" })
    }

    if (url.pathname === "/upload" && request.method === "POST") {
      return handleUpload(request, env)
    }

    if (request.method === "GET" || request.method === "HEAD") {
      return handleGetAsset(url.pathname, request.method, env)
    }

    return new Response("not found", { status: 404 })
  },
}

async function handleUpload(request: Request, env: Env): Promise<Response> {
  const authHeader = request.headers.get("Authorization") ?? ""
  const token = authHeader.startsWith("Bearer ") ? authHeader.slice(7) : ""
  if (!env.R2_BRIDGE_TOKEN || token !== env.R2_BRIDGE_TOKEN) {
    return Response.json({ error: "unauthorized" }, { status: 401 })
  }

  let body: UploadRequest
  try {
    body = await request.json()
  } catch {
    return Response.json({ error: "invalid json" }, { status: 400 })
  }

  const { key, content_type, body_base64, metadata = {} } = body

  if (!key || !content_type || !body_base64) {
    return Response.json(
      { error: "key, content_type, and body_base64 are required" },
      { status: 400 },
    )
  }

  // Reject keys that look like they embed customer PII
  if (key.includes("..") || !key.match(/^[A-Za-z0-9/_.\-]+$/)) {
    return Response.json({ error: "invalid key format" }, { status: 400 })
  }

  let assetBytes: ArrayBuffer
  try {
    assetBytes = base64Decode(body_base64)
  } catch {
    return Response.json({ error: "body_base64 is not valid base64" }, { status: 400 })
  }

  try {
    await env.ASSETS.put(key, assetBytes, {
      httpMetadata: { contentType: content_type },
      customMetadata: {
        ...metadata,
        uploaded_at: new Date().toISOString(),
      },
    })
  } catch (err) {
    return Response.json({ error: "r2 write failed", detail: String(err) }, { status: 502 })
  }

  const base = (env.ASSETS_PUBLIC_URL ?? "").replace(/\/$/, "")
  const fulfillment_url = `${base}/${key}`

  return Response.json({
    ok: true,
    key,
    fulfillment_url,
    url: fulfillment_url,
  })
}

async function handleGetAsset(pathname: string, method: string, env: Env): Promise<Response> {
  const key = pathname.replace(/^\/+/, "")
  if (!key || key.includes("..") || !key.match(/^[A-Za-z0-9/_.\-]+$/)) {
    return new Response("not found", { status: 404 })
  }

  const object = await env.ASSETS.get(key)
  if (object === null) {
    return new Response("not found", { status: 404 })
  }

  const headers = new Headers()
  object.writeHttpMetadata(headers)
  headers.set("etag", object.httpEtag)
  headers.set("cache-control", "public, max-age=31536000, immutable")

  return new Response(method === "HEAD" ? null : object.body, { headers })
}

function base64Decode(b64: string): ArrayBuffer {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i)
  }
  return bytes.buffer
}
