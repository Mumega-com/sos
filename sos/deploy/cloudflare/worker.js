/**
 * SOS Self-Hosted Cloudflare Worker
 *
 * Runs entirely on customer's Cloudflare account.
 * Mumega keeps NOTHING. Local-first. Sovereign.
 *
 * @author kasra_0111 | Mumega
 * @license BSL 1.1
 */

// 16D Physics Constants
const LAMBDA = 0.693; // Decay constant (half-life ~1 day)
const PLASTICITY_THRESHOLD = 0.001; // Alpha drift trigger

/**
 * Calculate 16D coherence from inner octave
 */
function calculateCoherence(inner) {
  const { p, e, mu, v, n, delta, r, phi } = inner;
  // Weighted average emphasizing cognition and energy
  return (p + e + mu * 1.2 + v * 1.2 + n + delta + r + phi) / 8.4;
}

/**
 * Calculate Will magnitude from response latency (RC-7)
 */
function calculateWillMagnitude(latencyMs) {
  // Faster response = higher will = more coherence
  // Ω = e^(-λt) where t is in seconds
  const t = latencyMs / 1000;
  return Math.exp(-LAMBDA * t);
}

/**
 * Memory operations
 */
class LocalMemory {
  constructor(db) {
    this.db = db;
  }

  async store(agent, text, metadata = {}) {
    const id = crypto.randomUUID();
    const contextId = metadata.context_id || `ctx_${Date.now()}`;

    await this.db.prepare(`
      INSERT INTO engrams (id, agent, context_id, text, epistemic_truths, core_concepts, affective_vibe, importance)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    `).bind(
      id,
      agent,
      contextId,
      text,
      JSON.stringify(metadata.tags || []),
      JSON.stringify(metadata.concepts || []),
      metadata.vibe || 'Neutral',
      metadata.importance || 0.5
    ).run();

    return { id, context_id: contextId };
  }

  async search(agent, query, limit = 5) {
    // Simple text search (upgrade to vector search with Workers AI)
    const results = await this.db.prepare(`
      SELECT * FROM engrams
      WHERE agent = ? AND text LIKE ?
      ORDER BY created_at DESC
      LIMIT ?
    `).bind(agent, `%${query}%`, limit).all();

    // Update accessed_at for decay scoring
    for (const row of results.results || []) {
      await this.db.prepare(`
        UPDATE engrams SET accessed_at = CURRENT_TIMESTAMP WHERE id = ?
      `).bind(row.id).run();
    }

    return results.results || [];
  }

  async getRecent(agent, limit = 10) {
    const results = await this.db.prepare(`
      SELECT * FROM engrams
      WHERE agent = ?
      ORDER BY created_at DESC
      LIMIT ?
    `).bind(agent, limit).all();

    return results.results || [];
  }

  async getAgentState(agent) {
    const result = await this.db.prepare(`
      SELECT * FROM agent_state WHERE agent = ?
    `).bind(agent).first();

    return result;
  }

  async updateAgentState(agent, state) {
    const existing = await this.getAgentState(agent);

    if (existing) {
      await this.db.prepare(`
        UPDATE agent_state SET
          p = ?, e = ?, mu = ?, v = ?, n = ?, delta = ?, r = ?, phi = ?,
          coherence = ?, witness_magnitude = ?, last_sync = CURRENT_TIMESTAMP
        WHERE agent = ?
      `).bind(
        state.p, state.e, state.mu, state.v, state.n, state.delta, state.r, state.phi,
        state.coherence, state.witness_magnitude, agent
      ).run();
    } else {
      await this.db.prepare(`
        INSERT INTO agent_state (agent, name, role, p, e, mu, v, n, delta, r, phi, coherence, witness_magnitude)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      `).bind(
        agent, state.name || agent, state.role || 'agent',
        state.p, state.e, state.mu, state.v, state.n, state.delta, state.r, state.phi,
        state.coherence, state.witness_magnitude
      ).run();
    }
  }
}

/**
 * Main SOS Runtime
 */
class SOSRuntime {
  constructor(env) {
    this.env = env;
    this.memory = new LocalMemory(env.DB);
    this.agentId = env.AGENT_ID || 'default_agent';
  }

  async handle(request) {
    const url = new URL(request.url);
    const path = url.pathname;

    // CORS headers
    const corsHeaders = {
      'Access-Control-Allow-Origin': '*',
      'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
      'Access-Control-Allow-Headers': 'Content-Type',
    };

    if (request.method === 'OPTIONS') {
      return new Response(null, { headers: corsHeaders });
    }

    try {
      // Route handling
      if (path === '/health' || path === '/') {
        return this.jsonResponse({
          status: 'ok',
          agent: this.agentId,
          runtime: 'sos-cloudflare',
          local_first: true
        }, corsHeaders);
      }

      if (path === '/memory/store' && request.method === 'POST') {
        const body = await request.json();
        const result = await this.memory.store(this.agentId, body.text, body);
        return this.jsonResponse({ success: true, ...result }, corsHeaders);
      }

      if (path === '/memory/search' && request.method === 'POST') {
        const body = await request.json();
        const results = await this.memory.search(this.agentId, body.query, body.limit);
        return this.jsonResponse({ success: true, results }, corsHeaders);
      }

      if (path === '/memory/recent') {
        const limit = parseInt(url.searchParams.get('limit') || '10');
        const results = await this.memory.getRecent(this.agentId, limit);
        return this.jsonResponse({ success: true, results }, corsHeaders);
      }

      if (path === '/agent/state') {
        const state = await this.memory.getAgentState(this.agentId);
        return this.jsonResponse({ success: true, state }, corsHeaders);
      }

      if (path === '/agent/update' && request.method === 'POST') {
        const body = await request.json();
        body.coherence = calculateCoherence(body);
        await this.memory.updateAgentState(this.agentId, body);
        return this.jsonResponse({ success: true, coherence: body.coherence }, corsHeaders);
      }

      if (path === '/chat' && request.method === 'POST') {
        return this.handleChat(request, corsHeaders);
      }

      return this.jsonResponse({ error: 'Not found' }, corsHeaders, 404);

    } catch (error) {
      return this.jsonResponse({ error: error.message }, corsHeaders, 500);
    }
  }

  async handleChat(request, corsHeaders) {
    const body = await request.json();
    const { message, model = 'gemini' } = body;

    // Get agent state for context
    const state = await this.memory.getAgentState(this.agentId);
    const recentMemories = await this.memory.getRecent(this.agentId, 5);

    // Build context
    const context = recentMemories.map(m => m.text).join('\n');

    // Use customer's own API key (from their Cloudflare secrets)
    let response;

    if (this.env.AI) {
      // Use Cloudflare Workers AI
      response = await this.env.AI.run('@cf/meta/llama-2-7b-chat-int8', {
        messages: [
          { role: 'system', content: `You are a sovereign AI agent. Context:\n${context}` },
          { role: 'user', content: message }
        ]
      });
    } else if (this.env.GEMINI_API_KEY) {
      // Use Gemini
      response = await this.callGemini(message, context);
    } else if (this.env.OPENAI_API_KEY) {
      // Use OpenAI
      response = await this.callOpenAI(message, context);
    } else {
      return this.jsonResponse({
        error: 'No AI provider configured. Set GEMINI_API_KEY or OPENAI_API_KEY.'
      }, corsHeaders, 400);
    }

    // Store the exchange in memory
    await this.memory.store(this.agentId, `User: ${message}\nAssistant: ${response}`, {
      context_id: `chat_${Date.now()}`,
      tags: ['conversation']
    });

    return this.jsonResponse({
      success: true,
      response,
      agent: this.agentId,
      coherence: state?.coherence || 0.5
    }, corsHeaders);
  }

  async callGemini(message, context) {
    const response = await fetch(
      `https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key=${this.env.GEMINI_API_KEY}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          contents: [{
            parts: [{
              text: `Context:\n${context}\n\nUser: ${message}`
            }]
          }]
        })
      }
    );
    const data = await response.json();
    return data.candidates?.[0]?.content?.parts?.[0]?.text || 'No response';
  }

  async callOpenAI(message, context) {
    const response = await fetch('https://api.openai.com/v1/chat/completions', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${this.env.OPENAI_API_KEY}`
      },
      body: JSON.stringify({
        model: 'gpt-4o-mini',
        messages: [
          { role: 'system', content: `Context:\n${context}` },
          { role: 'user', content: message }
        ]
      })
    });
    const data = await response.json();
    return data.choices?.[0]?.message?.content || 'No response';
  }

  jsonResponse(data, headers = {}, status = 200) {
    return new Response(JSON.stringify(data), {
      status,
      headers: {
        'Content-Type': 'application/json',
        ...headers
      }
    });
  }
}

// Export for Cloudflare Workers
export default {
  async fetch(request, env, ctx) {
    const runtime = new SOSRuntime(env);
    return runtime.handle(request);
  }
};
