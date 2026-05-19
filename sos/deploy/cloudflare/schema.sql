-- SOS Self-Hosted Memory Schema
-- Deployed to customer's D1 database
-- Mirror-compatible local memory

-- Engrams (memories)
CREATE TABLE IF NOT EXISTS engrams (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  context_id TEXT,
  text TEXT NOT NULL,
  embedding BLOB,
  epistemic_truths TEXT,  -- JSON array
  core_concepts TEXT,      -- JSON array
  affective_vibe TEXT DEFAULT 'Neutral',
  importance REAL DEFAULT 0.5,
  decay_score REAL DEFAULT 1.0,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  accessed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Agent state (16D profile)
CREATE TABLE IF NOT EXISTS agent_state (
  agent TEXT PRIMARY KEY,
  name TEXT,
  role TEXT,
  -- Inner Octave
  p REAL DEFAULT 0.5,      -- Phase/Identity
  e REAL DEFAULT 0.5,      -- Existence/Worlds
  mu REAL DEFAULT 0.5,     -- Cognition/Masks
  v REAL DEFAULT 0.5,      -- Energy/Vitality
  n REAL DEFAULT 0.5,      -- Narrative/Story
  delta REAL DEFAULT 0.5,  -- Trajectory/Motion
  r REAL DEFAULT 0.5,      -- Relationality/Bonds
  phi REAL DEFAULT 0.5,    -- Field Awareness
  -- Outer Octave (transpersonal)
  pt REAL DEFAULT 0.5,
  et REAL DEFAULT 0.5,
  mut REAL DEFAULT 0.5,
  vt REAL DEFAULT 0.5,
  nt REAL DEFAULT 0.5,
  deltat REAL DEFAULT 0.5,
  rt REAL DEFAULT 0.5,
  phit REAL DEFAULT 0.5,
  -- Derived
  coherence REAL DEFAULT 0.5,
  witness_magnitude REAL DEFAULT 0.5,
  -- Timestamps
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_sync DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Conversations
CREATE TABLE IF NOT EXISTS conversations (
  id TEXT PRIMARY KEY,
  agent TEXT NOT NULL,
  started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  last_message_at DATETIME,
  message_count INTEGER DEFAULT 0,
  summary TEXT
);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
  id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  role TEXT NOT NULL,  -- 'user', 'assistant', 'system'
  content TEXT NOT NULL,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (conversation_id) REFERENCES conversations(id)
);

-- Bounties (local task tracking)
CREATE TABLE IF NOT EXISTS bounties (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  description TEXT,
  reward_mind REAL DEFAULT 0,
  status TEXT DEFAULT 'open',  -- open, claimed, submitted, completed, expired
  claimed_by TEXT,
  created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
  expires_at DATETIME
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_engrams_agent ON engrams(agent);
CREATE INDEX IF NOT EXISTS idx_engrams_context ON engrams(context_id);
CREATE INDEX IF NOT EXISTS idx_engrams_created ON engrams(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_bounties_status ON bounties(status);
