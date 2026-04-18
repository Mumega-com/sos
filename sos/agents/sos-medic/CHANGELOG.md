# sos-medic Changelog

Semver: MAJOR = protocol break, MINOR = new capability, PATCH = wording/tuning.

## [1.0.0] — 2026-04-16
### Added
- Initial medic role spec: 5-step response protocol, pipe inventory, guardrails.
- Incident log format + template (see `BUG_REPORT.md`).
- Experience accumulation via `EXPERIENCE.md` (read on startup, append after fixes).
- Wake routing: bus message to `sos-medic` wakes this tmux via `agent-wake-daemon`.
- Model-agnostic: any CLI (Claude Code, Codex, Gemini) can load `CLAUDE.md` from this dir.
