# ToRivers Tools (Vendored)

Utility modules cherry-picked from `torivers-sdk` for use in Mumega skills.

## Why vendored?
- ToRivers SDK requires Python 3.11+, server runs 3.10
- SDK's `__init__.py` imports LangGraph (heavy dependency)
- These individual tool modules have clean, minimal imports

## Available modules

| Module | What it provides |
|--------|-----------------|
| `data.py` | `DataProcessor`, `DataStats` — structured data handling |
| `progress.py` | `ProgressReporter`, `ProgressEntry` — execution progress tracking |
| `http.py` | HTTP client with retry, rate limiting (needs httpx) |
| `llm.py` | Multi-model LLM wrapper with cost tracking |
| `storage.py` | R2/S3 storage tools |
| `credentials.py` | Credential vault proxy |

## Usage in Mumega skills

```python
from sos.vendors.torivers_tools.progress import ProgressReporter, ConsoleProgressReporter
from sos.vendors.torivers_tools.data import DataProcessor

reporter = ConsoleProgressReporter()
reporter.log("Starting audit...")
```

## Source
Copied from: `~/vendor/torivers-v2/torivers-sdk/src/torivers_sdk/`
Version: 0.2.0b1
License: Proprietary (ToRivers/Digid Inc.)

## When to update
When the ToRivers SDK updates, re-copy the individual files. Don't install the full SDK until the server is on Python 3.11+.
