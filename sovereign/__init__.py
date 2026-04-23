"""
Sovereign — Standalone business logic for the Mumega ecosystem.

Extracted from ~/cli/mumega/core/sovereign/ to be independent of the CLI monolith.
Every module talks to Mirror API via HTTP. No mumega.* imports.

Modules:
  hive_evolution  — Self-improving swarm recipes
  bounty_board    — Task bounties with $MIND rewards
  treasury        — Token wallet operations
  trust           — Agent trust/reputation scoring
  bank            — Sovereign bank (minting, rewards)
  genetics        — Agent DNA model
"""
