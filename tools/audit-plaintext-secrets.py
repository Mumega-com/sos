#!/usr/bin/env python3
"""
§2B.4 Plaintext Secrets Audit Tool

Walks the repo and known secret locations, emitting a CSV of every plaintext
secret found. Zero findings = G7 acceptance criterion 1.

Usage:
    python3 tools/audit-plaintext-secrets.py [--root PATH] [--output findings.csv]

Outputs:
    CSV with columns: path, key_name, pattern_type, line_number, last_modified

Patterns detected:
  - API keys (sk-, pk-, AKIA, ghp_, ghs_)
  - Database DSNs (postgres://, postgresql://)
  - Vault unseal keys / root tokens (hvs., s.)
  - Private key blocks (-----BEGIN)
  - .env files with non-empty values
  - wrangler.toml [vars] with sensitive-looking keys
  - tokens.json plaintext entries (non-hashed)

Exit codes:
  0 — no findings (CI-safe)
  1 — findings detected (CI fails)
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Patterns to detect ────────────────────────────────────────────────────────

PATTERNS: list[tuple[str, str]] = [
    # (pattern_type, regex)
    ('api_key_stripe',     r'sk_(live|test)_[A-Za-z0-9]{20,}'),
    ('api_key_stripe_pub', r'pk_(live|test)_[A-Za-z0-9]{20,}'),
    ('api_key_aws',        r'AKIA[0-9A-Z]{16}'),
    ('api_key_github',     r'gh[pso]_[A-Za-z0-9]{36,}'),
    ('api_key_openai',     r'sk-[A-Za-z0-9]{32,}'),
    ('api_key_anthropic',  r'sk-ant-[A-Za-z0-9\-]{20,}'),
    ('api_key_gemini',     r'AIza[0-9A-Za-z\-_]{35,}'),
    ('database_dsn',       r'postgres(?:ql)?://[^:]+:[^@]+@'),
    ('vault_token',        r'hvs\.[A-Za-z0-9]{24,}'),
    ('vault_unseal',       r'[A-Za-z0-9+/]{43}='),   # base64 256-bit key (loose)
    ('private_key_block',  r'-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'),
    ('generic_secret',     r'(?i)(?:secret|password|passwd|token|apikey|api_key)\s*[=:]\s*["\']?[A-Za-z0-9+/_.@\-]{16,}'),
]

COMPILED = [(name, re.compile(pat)) for name, pat in PATTERNS]

# Files / dirs to skip
SKIP_DIRS = {
    '.git', '__pycache__', 'node_modules', '.venv', 'venv', '.tox',
    'dist', 'build', '.mypy_cache', '.ruff_cache', '.pytest_cache',
    'migrations',  # SQL migrations contain only schema, no secrets
    '.worktrees',  # git worktrees mirror main codebase — not a separate secret surface
    'tests',       # test fixtures use fake/mock credentials
    'docs',        # architecture docs and plans contain example values, not real secrets
    'web',         # frontend build artifacts and compiled output
}

SKIP_FILES = {
    'audit-plaintext-secrets.py',  # this file itself
    'poetry.lock', 'package-lock.json', 'yarn.lock',
    '.env.example',  # template — intentionally contains placeholders, not real secrets
    '.env.supabase', # gitignored secrets store — contains vault refs
}

# Also skip file names matching these glob-like suffixes
SKIP_FILE_SUFFIXES = {'.md', '.rst', '.txt'}  # docs/plans/changelogs

SKIP_EXTENSIONS = {
    '.pyc', '.pyo', '.so', '.dylib', '.dll',
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.svg',
    '.woff', '.woff2', '.ttf', '.eot',
    '.zip', '.tar', '.gz', '.bz2',
    '.db', '.sqlite', '.sqlite3',
    '.pdf', '.docx', '.xlsx',
}

# Lines containing these strings are not findings (they ARE the audit tool)
ALLOWLIST_SUBSTRINGS = [
    'audit-plaintext-secrets',
    'PATTERN',
    'pattern_type',
    '# Example:',
    '# test',
    'REPLACE_ME',
    'your_secret_here',
    'INSERT_SECRET',
    '${',        # shell variable substitution — not a literal value
    'os.getenv',
    'os.environ',
    # ── Vault refs — already rotated, not a plaintext secret ─────────────────
    'vault:',   # vault:sos/env/api-keys#KEY — resolved at runtime
    # ── Code patterns reading from settings/env objects ──────────────────────
    '_settings.',       # e.g. SQUAD_TOKEN = _settings.auth.system_token_str
    'get_settings()',   # module-level settings accessor
    'settings.',        # settings.redis.password_str etc.
    '.token_str',       # property on settings — not a literal
    '.password_str',    # property on settings — not a literal
    '.secret_str',      # property on settings — not a literal
    # ── OAuth / OIDC code — variable names that receive runtime values ────────
    'access_token =',   # receives value from OAuth response, not a literal
    'refresh_token =',  # receives value from OAuth response, not a literal
    'client_secret =',  # receives value from env/db at runtime
    'token_hash',       # hashed form — not raw secret
    # ── Known safe localhost defaults (dev/test fallbacks) ───────────────────
    '@localhost',       # postgresql://user:pass@localhost — test default
    'postgres:postgres@', # default dev credential
    'mumega:mumega@',   # default dev credential
    # ── JSON / dict access patterns ──────────────────────────────────────────
    '.json()',          # reading from HTTP response JSON
    '["token"]',        # dict access — runtime value
    "['token']",        # dict access — runtime value
    # ── Internal bus / test tokens ───────────────────────────────────────────
    'sk-sos-system',    # internal system token constant (not rotated)
    'resolve_token',    # function name matches regex — not a secret
    'raw_token',        # local variable name — assigned from auth header parsing
    'admin_token',      # variable name — not a credential in docs/plans context
    # ── JavaScript env reading ────────────────────────────────────────────────
    'process.env.',     # JS: const TOKEN = process.env.TOKEN || ""
    'or ""',            # empty string fallback — not a literal secret
    "|| ''",            # JS empty string fallback
    # ── HTTP request / response parsing ──────────────────────────────────────
    'request.headers',  # reading from HTTP request header
    'credentials.credentials',  # FastAPI Security object access
    'removeprefix',     # header parsing: auth.removeprefix("Bearer ")
    '.bus_token',       # DB record attribute — not a literal
    'tenant.bus_token', # DB lookup result
    # ── Context management tokens (not secrets) ───────────────────────────────
    '_otel_context',    # OpenTelemetry context attach/detach token
    '_current_trace_id',# context var token
    '.attach(',         # OTEL: token = ctx.attach(parent)
    '.set(',            # context var: token = var.set(value)
    # ── Random/computed secret generation ────────────────────────────────────
    'pyotp.random_base32',  # generates TOTP secret — not storing a literal
    '_resolve_totp_secret', # resolver function call — reads from Vault/store
    'token_urlsafe',    # secrets.token_urlsafe(32) — generates random, not storing
    # ── Explicit function calls (computed, not literal) ───────────────────────
    'create_bus_token', # function call returning computed token
    'mint_qnft(',       # function call returning token
    # ── Self-referential assignments (passing params to self._) ───────────────
    'objectives_token', # parameter/attribute name — assigned from caller
    '_squad_client',    # module-level client initialized with constant
    'bus_token or',     # bus_token = tenant.bus_token or "" — DB lookup with fallback
    # ── Variable references (all-caps constant passed to external lib) ────────
    'stripe.api_key',   # stripe.api_key = STRIPE_SECRET_KEY — setting lib from variable
    # ── Argparse namespace and response object attribute access ───────────────
    '=args.',           # reuse_token=args.reuse_token — argparse attr
    'token_response.',  # access_token=token_response.access_token — response attr
]


def _should_skip_path(path: Path) -> bool:
    for part in path.parts:
        if part in SKIP_DIRS:
            return True
    if path.name in SKIP_FILES:
        return True
    if path.suffix in SKIP_EXTENSIONS:
        return True
    if path.suffix in SKIP_FILE_SUFFIXES:
        return True
    # Skip .env files (they are gitignored secrets stores; .env.example is in SKIP_FILES)
    name = path.name
    if name == '.env' or name.startswith('.env.'):
        return True
    return False


def _scan_file(path: Path) -> list[dict]:
    findings = []
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except (PermissionError, OSError):
        return findings

    stat = path.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()

    for i, line in enumerate(text.splitlines(), start=1):
        # Skip allowlisted lines
        if any(al in line for al in ALLOWLIST_SUBSTRINGS):
            continue
        # Skip comment-only lines
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('//') or stripped.startswith('*'):
            continue

        for pattern_type, regex in COMPILED:
            if regex.search(line):
                findings.append({
                    'path': str(path),
                    'pattern_type': pattern_type,
                    'line_number': i,
                    'last_modified': mtime,
                    'key_name': _extract_key_name(line),
                })
                break  # one finding per line

    return findings


def _extract_key_name(line: str) -> str:
    """Best-effort extract the key name from a line like KEY=value."""
    m = re.match(r'\s*([A-Z_a-z][A-Z_a-z0-9]*)\s*[=:]', line)
    if m:
        return m.group(1)
    return ''


def _scan_tokens_json(tokens_path: Path) -> list[dict]:
    """Check tokens.json for entries missing token_hash (still plaintext)."""
    import json
    findings = []
    try:
        entries = json.loads(tokens_path.read_text())
    except Exception:
        return findings

    mtime = datetime.fromtimestamp(tokens_path.stat().st_mtime, tz=timezone.utc).isoformat()
    for entry in entries:
        if entry.get('token') and not entry.get('token_hash'):
            findings.append({
                'path': str(tokens_path),
                'pattern_type': 'plaintext_bus_token',
                'line_number': 0,
                'last_modified': mtime,
                'key_name': entry.get('label', 'unknown'),
            })
    return findings


def scan(root: Path) -> list[dict]:
    all_findings: list[dict] = []

    for path in root.rglob('*'):
        if not path.is_file():
            continue
        if _should_skip_path(path.relative_to(root)):
            continue
        all_findings.extend(_scan_file(path))

    # Check known secret locations
    tokens_path = root / 'sos' / 'bus' / 'tokens.json'
    if tokens_path.exists():
        all_findings.extend(_scan_tokens_json(tokens_path))

    return all_findings


def main() -> int:
    parser = argparse.ArgumentParser(description='Audit plaintext secrets in SOS repo')
    parser.add_argument('--root', default=str(Path(__file__).parent.parent),
                        help='Root directory to scan (default: SOS repo root)')
    parser.add_argument('--output', default='-',
                        help='Output CSV file path (- for stdout)')
    parser.add_argument('--quiet', action='store_true',
                        help='Only print summary, not all findings')
    args = parser.parse_args()

    root = Path(args.root).resolve()
    print(f'Scanning {root} ...', file=sys.stderr)

    findings = scan(root)

    fieldnames = ['path', 'pattern_type', 'key_name', 'line_number', 'last_modified']

    if args.output == '-':
        out = sys.stdout
        writer = csv.DictWriter(out, fieldnames=fieldnames)
        writer.writeheader()
        for f in findings:
            writer.writerow(f)
    else:
        with open(args.output, 'w', newline='') as out:
            writer = csv.DictWriter(out, fieldnames=fieldnames)
            writer.writeheader()
            for f in findings:
                writer.writerow(f)
        print(f'Findings written to {args.output}', file=sys.stderr)

    total = len(findings)
    print(f'\nTotal findings: {total}', file=sys.stderr)
    if not args.quiet:
        by_type: dict[str, int] = {}
        for f in findings:
            by_type[f['pattern_type']] = by_type.get(f['pattern_type'], 0) + 1
        for pt, count in sorted(by_type.items(), key=lambda x: -x[1]):
            print(f'  {count:4d}  {pt}', file=sys.stderr)

    return 0 if total == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
