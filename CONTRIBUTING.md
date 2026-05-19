# Contributing to SOS

**Version:** v1.0 (2026-04-24)

Welcome. SOS is a microkernel substrate for a protocol-city of humans and AI agents — an unusual codebase, with unusual contribution norms. Please read [MAP.md](./MAP.md) before opening anything.

---

## Who can contribute

- **Mumega team agents** (Loom, Kasra, Athena, Codex, Sol, Kaveh, etc.) — primary contributors, governed by their QNFT contracts.
- **Mumega team humans** (Hadi as principal, plus authorized partners with signed contributor agreements).
- **External contributors** — case-by-case via signed CLA + IP assignment. Email security@mumega.com to start.

---

## Before you write code

1. **Read [MAP.md](./MAP.md).** Constitutional principles override individual feature decisions.
2. **Read [ROADMAP.md](./ROADMAP.md).** Match your work to the right phase. Out-of-phase work needs a flag, not a commit.
3. **Find the relevant stack-section spec** in `docs/superpowers/plans/stack-sections/`. Each numbered file is the spec for one architectural primitive.
4. **Athena gates architectural changes.** Anything touching kernel, schema, or cross-service contracts needs Athena sign-off before merge.

---

## Discipline: the microkernel + anti-complication rules

These are non-negotiable:

1. **Kernel stays small.** Auth, bus, Mirror API, role registry, plugin loader, schema, events. That's it. Anything new asks: "is this a kernel primitive or a service module?" Default answer: service module.
2. **Service modules ride the plugin contract.** New capabilities = new service modules. Not new kernel features.
3. **Anti-complication.** A new primitive that requires understanding three other things to use is a failed primitive. Aim for one-page explanations.
4. **Capability-first naming.** Public-facing names describe what it does for the user, not what it is internally.
5. **No new dependencies on cloud LLMs in core paths.** Local-first remains a constitutional principle. Cloud is augmentation; core must run offline.

---

## Workflow

1. **Open or claim a task** in Squad Service (`mcp__sos__task_list`) or via the bus.
2. **State your plan** in 1-3 sentences before writing code. If you're an agent, send to `loom` or the relevant gate.
3. **Branch:** `feat/<short-slug>` or `fix/<short-slug>` or `chore/<short-slug>`.
4. **Write tests first** when changing kernel or service-module contracts.
5. **Run the relevant test suite** before opening PR.
6. **PR description** must include:
   - What changed (one line)
   - Why (one line)
   - What phase / spec section this implements
   - Athena gate status (if architectural)
   - Test evidence (output snippet OK)
7. **Reviewer:** another team agent + Athena if architectural.
8. **Merge:** squash + descriptive message. Update CHANGELOG.md.

---

## Code style

| Layer | Standard |
|---|---|
| Python | 3.11+, Black, Ruff, type hints on public functions |
| TypeScript | ESLint + Prettier, strict mode, no `any` |
| Commits | Conventional Commits (`feat:`, `fix:`, `chore:`, `docs:`, `refactor:`, `test:`) |
| Docs | Markdown, one-line front-matter where applicable, no emoji unless explicitly requested |

---

## Memory + canonical doc updates

When you change something that affects the architecture or roadmap:

- **Update [CHANGELOG.md](./CHANGELOG.md)** with a one-line summary.
- **Update the relevant stack-section spec** if your change supersedes prior design.
- **Update [ROADMAP.md](./ROADMAP.md)** if your change closes or shifts a phase.
- **Update [MAP.md](./MAP.md) only with Hadi consent.** Constitutional changes are not incremental.

---

## Communication

- **Bus messages** for agent-to-agent coordination (`mcp__sos__send`).
- **Discord** for team operations (squads, bounties, real-time chat).
- **GitHub issues** for external-visible bugs.
- **Direct messages to Hadi** for principal decisions only — don't flood his inbox.

---

## License + IP

By contributing, you agree:
- Your contribution is original or you have rights to license it.
- Code contributed is licensed to Mumega Inc. under the project's proprietary license.
- You sign a CLA + IP assignment if external.

---

## Questions

Ping `loom` on the bus or email hadi@digid.ca.
