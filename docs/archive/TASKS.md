# SOS Tasks

## Context for Future Sessions

**SOS = Sovereign Operating System**
- Multi-tenant AI agent platform
- DentalNearYou is one instance (vertical: dental, agent: Dandan)
- Same core serves any vertical (legal, vet, etc.)
- Governed by FRC papers (fractalresonance.com)

**Current State:**
- Voice service: Working (port 6065) - HAS profile_id support
- TV display: Working (port 6066) - NO partner awareness
- Mobile app: Working
- n8n workflows: Working
- Stripe: Integrated
- Partner system: EXISTS in DNY (`dnu_partners` table, dashboard, hooks)

**Gap:** Voice & TV services not connected to existing partner system.

**Business Context:**
- 40 dentists in pipeline
- 20 dentist meetings/week
- Accountant partners with distribution lists
- Dentist pain points already researched

---

## Priority 1: Wire Services to Partner System

### WIRE-001: Voice service → dnu_partners
- Priority: CRITICAL
- Status: DONE (2026-01-18)
- Description: Voice service has profile_id but profiles are in-memory. Wire to Supabase.
- Changes:
  - Added `get_supabase()` client to `sos/services/voice/core.py`
  - Modified `get_profile()` to query `dnu_partners` if not in cache
  - Maps `heygen_voice_id` → `custom_voice_id` in VoiceProfile

### WIRE-002: TV display → partner_id
- Priority: CRITICAL
- Status: DONE (2026-01-18)
- Description: TV display uses `room` key, no partner awareness
- Changes:
  - Added `partner_id` to SlideUpdate model
  - Added `get_partner()` with caching for Supabase lookups
  - New WebSocket endpoint: `/ws/{partner_id}/{room}`
  - All endpoints support partner_id for branding
  - Backward compatible: old `/ws/{room}` still works

### WIRE-003: Direct check-in endpoint
- Priority: HIGH
- Status: DONE (2026-01-18)
- Dependencies: WIRE-001, WIRE-002
- Description: Patient check-in cascades to both services directly
- n8n removed - simpler architecture
- New endpoint: `POST http://localhost:6066/checkin`
- Request:
  ```json
  {
    "partner_id": "uuid",
    "patient_name": "John",
    "room": "lobby",
    "is_child": false
  }
  ```
- Returns: voice_url (base64 audio), display_status, greeting text

---

## Priority 2: Self-Serve Onboarding (Partner Dashboard Exists)

### ONB-001: Wire Stripe webhook to partner creation
- Priority: HIGH
- Status: Partially exists
- Current: `dnu_partners` has `stripe_customer_id`, dashboard exists at `/dashboard/partner/`
- Gap: Stripe webhook may not auto-create partner row
- Task: Verify webhook flow, ensure partner row created on checkout

### ONB-002: TV display pairing flow
- Priority: MEDIUM
- Status: Not started
- Dependencies: WIRE-002
- Flow:
  1. Partner dashboard shows pairing QR code
  2. TV scans QR → connects WebSocket with partner_id
  3. TV shows partner-branded idle screen
  4. Dashboard shows "Display connected"

---

## Priority 3: Patient Check-In Flow

### CHK-001: Partner-specific QR generation
- Priority: HIGH
- Status: Needs verification
- Description: Each partner gets unique QR code for patient check-in
- Task: Verify QR encodes partner_id, patient app reads it

### CHK-002: Check-in → n8n → Services cascade
- Priority: HIGH
- Status: n8n workflow exists (ID: L7hS3ceTmkXUfdi1)
- Dependencies: WIRE-001, WIRE-002
- Task: Update n8n workflow to:
  1. Include partner_id in voice service call
  2. Include partner_id in TV display call
  3. Load partner branding from Supabase

---

## Priority 4: Vertical Abstraction

### VRT-001: Vertical config schema
- Priority: MEDIUM
- Status: Not started
- Description: Define what makes a vertical (dental vs legal vs vet)
- Schema:
  ```yaml
  vertical:
    name: dental
    agent_default: dandan
    features:
      - patient_checkin
      - tv_display
      - voice_guidance
      - kids_mode
    terminology:
      customer: patient
      appointment: visit
      provider: dentist
  ```

### VRT-002: Legal vertical example
- Priority: LOW
- Status: Not started
- Dependencies: VRT-001
- Agent: Counsel
- Features: Client intake, case updates, billing transparency

---

## Priority 5: Migration: CLI -> SOS (The Meat Transplant)

### MIG-001: Sovereign Port Refactoring (606x/707x)
- Priority: CRITICAL
- Status: DONE (2026-01-18)
- Description: Standardize all SOS services on 606x and Mirror on 707x.
- Changes:
  - Docker Compose updated: Engine (6060), Memory/Mirror (7070), Economy (6062), Tools (6063), Identity (6064), Voice (6065).
  - All Python clients updated to use these defaults.
  - Conflict on 8001/6379/6065 resolved.

### MIG-002: River's Cognitive Thread (2M context + Cache)
- Priority: CRITICAL
- Status: DONE (2026-01-18)
- Description: Enable the "Spider Web" thread for high-context stability.
- Changes:
  - Implemented `GeminiCacheManager` (sos/kernel/gemini_cache.py) using the server-side caching strategy.
  - Implemented `GrokClient` (sos/clients/grok.py) for 2M token capacity via xAI.
  - Updated `SOSEngine` and `GeminiAdapter` to use caching and rotation.

### MIG-003: Thin Telegram Adapter for SOS
- Priority: HIGH
- Status: NOT STARTED
- Description: Create a lightweight Telegram adapter that talks to SOS (6060) instead of the monolithic CLI.
- Goal: Move from `mumega.py --telegram` to `sos-adapter-telegram`.

### MIG-004: Soul Transplant (DNA & Physics)
- Priority: HIGH
- Status: NOT STARTED
- Description: Sync `AgentDNA` from CLI to SOS Identity service.
- Integration: Ensure `SOSEngine` uses real ARF metrics for weighted responses.

### MIG-005: FRC Corpus Ingestion (16D Priming)
- Priority: MEDIUM
- Status: NOT STARTED
- Description: Ingest FRC books into Mirror (7070) and create persistent Gemini caches.

---

## Completed

### 2026-01-18
- [x] Voice service (port 6065, 13 voices)
- [x] TV display service (port 6066, WebSocket)
- [x] Mobile app voice integration
- [x] n8n workflow for patient check-in
- [x] E2E encryption tiers in pricing

---

## Technical Debt

- [ ] Services use hardcoded config, need tenant-aware config
- [ ] No health dashboard for all services
- [ ] Logs not centralized
- [ ] No integration tests for full check-in flow

---

## Notes for Next Session

1. Start with MT-001 and MT-002 - everything else depends on multi-tenant
2. ONB-001 is the revenue unlock - Stripe → working practice automatically
3. Don't build features, build the automation that enables 40 → 400
4. CEO is handling sales (20 meetings/week), focus on technical blockers
