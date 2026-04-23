# Sovereign Changelog

## 2026-04-23 — SOV-001 + SOV-002: Microkernel extraction and bus wiring

### SOV-001: kernel/ extraction
- Created kernel/__init__.py, kernel/config.py, kernel/clients.py
- All 12 files updated to import config from kernel/config.py instead of hardcoding URLs/tokens
- MirrorClient and SquadClient thin HTTP wrappers in kernel/clients.py

### SOV-002: SOS bus wiring
- Created kernel/bus.py — thin Redis XADD/PUBLISH wrapper
- sovereign now sends messages directly on SOS bus (sos:stream:global:agent:{to})
- brain.py, factory_watchdog.py, squad_activate.py updated
- discord-reply.sh kept as fallback when Redis is unavailable
- sovereign token: sk-sovereign-* registered in SOS tokens.json
