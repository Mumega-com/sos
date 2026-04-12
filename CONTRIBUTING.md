# Contributing to SOS

Thanks for your interest. SOS is actively developed — contributions are welcome.

## Before you start

- Open an issue first for anything beyond a typo or obvious bug fix.
- If you're fixing a bug, include a reproduction case.
- If you're proposing a feature, explain the use case.

## Setup

```bash
git clone https://github.com/Mumega-com/sos.git
cd sos
pip install -e ".[dev]"
cp .env.example .env
cp sos/bus/tokens.json.example sos/bus/tokens.json
```

## Code style

- Python 3.11+, type hints on all public functions
- `black` for formatting, `ruff` for linting
- Run both before committing: `black . && ruff check .`
- No bare `except` — catch specific exceptions
- No `print()` in production code — use `logging`

## Testing

```bash
pytest tests/
```

Tests live in `tests/`. Add a test for any new behavior. If the existing test suite doesn't cover something you're changing, add coverage.

## Adding a skill

Skills are packaged capabilities agents can call. Each skill lives in `sos/skills/<skill-name>/` and requires:

1. `SKILL.md` — name, description, input/output schema, trust tier
2. Implementation file (Python)
3. Registration in `sos/services/squad/skills.py`

See existing skills in `sos/skills/` for examples.

## Adding an MCP tool

MCP tools are exposed to connected agents. They live in `sos/mcp/sos_mcp_sse.py`.

To add a tool:
1. Add a handler function
2. Register it in the tools list with name, description, and input schema
3. Add it to the tool dispatch table

## Submitting a PR

- One logical change per PR
- Clear description: what changed and why
- Tests pass, linting passes
- Update the README if your change affects the public interface

## What we won't merge

- Features without tests
- Breaking changes to the MCP tool interface without a migration path
- New dependencies that add significant bundle weight without clear benefit
- Code that hardcodes secrets, paths, or credentials

## Questions

Open an issue with the `question` label.
