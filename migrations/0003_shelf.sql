-- Phase 7 Step 7.6 — Shelf commerce schema.
-- Targets SQLite + D1 (shared SQL dialect for the columns we use).
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS shelf_products (
    id TEXT NOT NULL,
    tenant TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'usd',
    grant_id TEXT NOT NULL,
    mind_multiplier REAL NOT NULL DEFAULT 1.0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL,
    PRIMARY KEY (tenant, id)
);

CREATE INDEX IF NOT EXISTS idx_shelf_products_tenant
    ON shelf_products(tenant, active);

CREATE TABLE IF NOT EXISTS shelf_captures (
    id TEXT PRIMARY KEY,
    tenant TEXT NOT NULL,
    product_id TEXT NOT NULL,
    stripe_session_id TEXT NOT NULL UNIQUE,
    amount_cents INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'usd',
    buyer_email TEXT,
    mind_credited REAL NOT NULL DEFAULT 0.0,
    captured_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_shelf_captures_tenant
    ON shelf_captures(tenant, captured_at DESC);
