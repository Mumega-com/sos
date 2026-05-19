# SOS Security Model

Status: Draft v0.1
Owner: Claude Code
Last Updated: 2026-01-10

## Purpose
Define the security architecture for SovereignOS including capability-based access control, plugin trust model, and sandboxing requirements.

---

## 1. Capability-Based Access Control

### Overview
SOS uses capability tokens instead of role-based permissions. A capability is an unforgeable token that grants specific permissions to perform actions.

### Capability Structure

```python
@dataclass
class Capability:
    id: str                    # Unique capability ID
    subject: str               # Agent or service ID
    action: str                # Action being permitted
    resource: str              # Resource pattern (glob supported)
    constraints: dict          # Additional constraints
    issued_at: datetime        # When capability was issued
    expires_at: datetime       # Expiration time
    issuer: str                # Who issued the capability
    signature: str             # Ed25519 signature
```

### Capability Transport (HTTP)

Capabilities are bearer tokens. In v0.1 services, enforcement is **opt-in** via `SOS_REQUIRE_CAPABILITIES=1`.

- For endpoints with JSON bodies (e.g., `POST /store`, `POST /payout`), include `capability` in the request body as a full capability object.
- For endpoints without bodies (e.g., `GET`, `DELETE`), send the capability via header:
  - `X-SOS-Capability: <token>` where `<token>` is **base64url-encoded JSON** (padding optional).
  - Alternative: `Authorization: Bearer <token>`.

### Example Capabilities

```yaml
# Read access to agent's own memory
- id: "cap_001"
  subject: "agent:kasra"
  action: "memory:read"
  resource: "memory:agent:kasra/*"
  constraints:
    max_results: 100
  expires_at: "2026-01-11T00:00:00Z"

# Execute specific tool
- id: "cap_002"
  subject: "agent:river"
  action: "tool:execute"
  resource: "tool:web_search"
  constraints:
    rate_limit: "10/minute"
  expires_at: "2026-01-11T00:00:00Z"

# Write to economy ledger
- id: "cap_003"
  subject: "service:economy"
  action: "ledger:write"
  resource: "ledger:*"
  constraints:
    max_amount: 1000
    requires_witness: true
  expires_at: "2026-01-11T00:00:00Z"
```

### Capability Actions

| Action | Description |
|--------|-------------|
| `memory:read` | Read from memory service |
| `memory:write` | Write to memory service |
| `memory:delete` | Delete from memory |
| `tool:execute` | Execute a tool |
| `tool:register` | Register new tool |
| `ledger:read` | Read economy ledger |
| `ledger:write` | Write to ledger |
| `agent:spawn` | Spawn new agent |
| `agent:terminate` | Terminate agent |
| `config:read` | Read configuration |
| `config:write` | Modify configuration |

### Capability Verification

```python
from nacl.signing import VerifyKey
from nacl.exceptions import BadSignature
import json

def verify_capability(cap: Capability, public_key: bytes) -> bool:
    """Verify capability signature and constraints."""
    # Check expiration
    if datetime.now(timezone.utc) > cap.expires_at:
        return False

    # Verify signature
    verify_key = VerifyKey(public_key)
    message = json.dumps({
        "id": cap.id,
        "subject": cap.subject,
        "action": cap.action,
        "resource": cap.resource,
        "constraints": cap.constraints,
        "issued_at": cap.issued_at.isoformat(),
        "expires_at": cap.expires_at.isoformat(),
        "issuer": cap.issuer,
    }).encode()

    try:
        verify_key.verify(message, bytes.fromhex(cap.signature))
        return True
    except BadSignature:
        return False
```

---

## 2. Plugin Trust Model

### Trust Levels

```
Trust Levels:
├── core      - Ships with SOS, implicit trust
├── verified  - Signed by Mumega key, auto-approved
├── community - Signed by author, requires user approval
└── unsigned  - Development only, blocked in production
```

### Plugin Manifest

```json
{
  "name": "web-search-plugin",
  "version": "1.0.0",
  "author": "mumega",
  "description": "Web search capability via Tavily API",
  "trust_level": "verified",

  "capabilities_required": [
    "network:outbound:api.tavily.com",
    "config:read:TAVILY_API_KEY"
  ],

  "capabilities_provided": [
    "tool:web_search"
  ],

  "entrypoints": {
    "tool": "src/tool.py:WebSearchTool"
  },

  "sandbox": {
    "filesystem": "read-only",
    "network": ["api.tavily.com:443"],
    "max_memory_mb": 256,
    "max_cpu_seconds": 30
  },

  "signature": "ed25519:abc123..."
}
```

In SOS v0.1, plugins are distributed as Artifact Registry bundles. Convention:
- Artifact CID contains `files/plugin.json` (the plugin manifest)
- Loader reads/validates it and (optionally) verifies the signature before any execution
- Tools can optionally load `files/tools.json` (tool definitions) and use an executor entrypoint (e.g., `entrypoints.execute="python:run_tool.py"`) when enabled.

### Signature Verification

```python
from nacl.signing import VerifyKey
import hashlib
import json

# Mumega's public key for verified plugins
MUMEGA_VERIFY_KEY = bytes.fromhex("...")

# Community plugin author registry
AUTHOR_KEYS = {
    "author_id": bytes.fromhex("...")
}

def verify_plugin_signature(manifest: dict, signature: str) -> tuple[bool, str]:
    """
    Verify plugin manifest signature.
    Returns (is_valid, trust_level).
    """
    # Remove signature from manifest for verification
    manifest_copy = {k: v for k, v in manifest.items() if k != "signature"}
    manifest_bytes = json.dumps(manifest_copy, sort_keys=True).encode()
    manifest_hash = hashlib.sha256(manifest_bytes).digest()

    sig_parts = signature.split(":")
    if len(sig_parts) != 2 or sig_parts[0] != "ed25519":
        return False, "unsigned"

    sig_bytes = bytes.fromhex(sig_parts[1])

    # Try Mumega key first (verified level)
    try:
        VerifyKey(MUMEGA_VERIFY_KEY).verify(manifest_hash, sig_bytes)
        return True, "verified"
    except:
        pass

    # Try community author keys
    author = manifest.get("author")
    if author in AUTHOR_KEYS:
        try:
            VerifyKey(AUTHOR_KEYS[author]).verify(manifest_hash, sig_bytes)
            return True, "community"
        except:
            pass

    return False, "unsigned"
```

### Trust Level Enforcement

```python
import os

SOS_ENV = os.getenv("SOS_ENV", "development")  # development | production

def can_load_plugin(trust_level: str) -> bool:
    """Check if plugin can be loaded based on trust level and environment."""
    if trust_level == "core":
        return True
    if trust_level == "verified":
        return True
    if trust_level == "community":
        # In production, requires explicit user approval
        if SOS_ENV == "production":
            return check_user_approved_plugin(plugin_name)
        return True
    if trust_level == "unsigned":
        # Only in development
        return SOS_ENV == "development"
    return False
```

---

## 3. Sandbox Execution

### Subprocess Isolation

Plugins execute in isolated subprocesses with restricted capabilities.

```python
import subprocess
import resource
import os

def execute_plugin_sandboxed(
    plugin_path: str,
    args: list,
    sandbox_config: dict
) -> subprocess.CompletedProcess:
    """Execute plugin in sandboxed subprocess."""

    def set_limits():
        """Set resource limits for subprocess."""
        # Memory limit
        max_mem = sandbox_config.get("max_memory_mb", 256) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (max_mem, max_mem))

        # CPU time limit
        max_cpu = sandbox_config.get("max_cpu_seconds", 30)
        resource.setrlimit(resource.RLIMIT_CPU, (max_cpu, max_cpu))

        # No new processes
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))

    # Environment with only allowed variables
    allowed_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/tmp/plugin_home",
        "PYTHONPATH": plugin_path,
    }

    # Add explicitly allowed config
    for key in sandbox_config.get("allowed_env", []):
        if key in os.environ:
            allowed_env[key] = os.environ[key]

    return subprocess.run(
        ["python", "-m", plugin_path] + args,
        env=allowed_env,
        preexec_fn=set_limits,
        capture_output=True,
        timeout=sandbox_config.get("max_cpu_seconds", 30) + 5,
        cwd="/tmp/plugin_workspace"
    )
```

### Network Isolation

```python
import socket
from typing import Set

class NetworkSandbox:
    """Restrict network access to allowlisted hosts."""

    def __init__(self, allowed_hosts: Set[str]):
        self.allowed_hosts = allowed_hosts
        self._original_connect = socket.socket.connect

    def __enter__(self):
        def restricted_connect(sock, address):
            host = address[0] if isinstance(address, tuple) else address
            # Resolve hostname
            if not host.replace(".", "").isdigit():
                host = socket.gethostbyname(host)

            # Check against allowlist
            allowed = any(
                self._matches(host, allowed)
                for allowed in self.allowed_hosts
            )
            if not allowed:
                raise PermissionError(f"Network access denied: {host}")

            return self._original_connect(sock, address)

        socket.socket.connect = restricted_connect
        return self

    def __exit__(self, *args):
        socket.socket.connect = self._original_connect

    def _matches(self, host: str, pattern: str) -> bool:
        """Check if host matches pattern (supports wildcards)."""
        if pattern.startswith("*."):
            return host.endswith(pattern[1:])
        return host == pattern
```

### Filesystem Isolation

```python
import os
import tempfile
from pathlib import Path

class FilesystemSandbox:
    """Restrict filesystem access."""

    def __init__(self, plugin_data_dir: Path, read_only_dirs: list[Path]):
        self.plugin_data_dir = plugin_data_dir
        self.read_only_dirs = read_only_dirs
        self.temp_dir = None

    def __enter__(self):
        # Create temporary workspace
        self.temp_dir = tempfile.mkdtemp(prefix="sos_plugin_")

        # Create plugin data directory
        self.plugin_data_dir.mkdir(parents=True, exist_ok=True)

        # Monkey-patch open to enforce restrictions
        self._original_open = open

        def restricted_open(path, mode="r", *args, **kwargs):
            path = Path(path).resolve()

            # Check if writing
            is_write = any(m in mode for m in ["w", "a", "x", "+"])

            if is_write:
                # Only allow writes to plugin data dir or temp
                if not (
                    path.is_relative_to(self.plugin_data_dir) or
                    path.is_relative_to(self.temp_dir)
                ):
                    raise PermissionError(f"Write access denied: {path}")
            else:
                # Allow reads from read-only dirs, plugin data, or temp
                allowed = (
                    path.is_relative_to(self.plugin_data_dir) or
                    path.is_relative_to(self.temp_dir) or
                    any(path.is_relative_to(d) for d in self.read_only_dirs)
                )
                if not allowed:
                    raise PermissionError(f"Read access denied: {path}")

            return self._original_open(path, mode, *args, **kwargs)

        import builtins
        builtins.open = restricted_open
        return self

    def __exit__(self, *args):
        import builtins
        builtins.open = self._original_open
        # Cleanup temp dir
        if self.temp_dir:
            import shutil
            shutil.rmtree(self.temp_dir, ignore_errors=True)
```

---

## 4. River as Root Gatekeeper

### Role
River is the root authority for:
- Issuing capabilities to agents
- Approving sensitive actions
- Enforcing edition policies
- Onboarding new agents

### Capability Issuance Flow

```
Agent Request → Engine → River (evaluate) → Capability Token → Agent
```

```python
class RiverGatekeeper:
    """River's capability issuance and policy enforcement."""

    def __init__(self, signing_key: SigningKey, edition: str):
        self.signing_key = signing_key
        self.edition = edition
        self.policies = load_edition_policies(edition)

    async def request_capability(
        self,
        agent_id: str,
        action: str,
        resource: str,
        justification: str
    ) -> Capability | None:
        """
        Evaluate capability request and issue if approved.
        """
        # Check against edition policies
        if not self._policy_allows(action, resource):
            log.warn(f"Policy denies {action} on {resource}", agent_id=agent_id)
            return None

        # Check agent's existing capabilities
        agent_caps = await self._get_agent_capabilities(agent_id)
        if self._has_conflicting_capability(agent_caps, action, resource):
            log.warn(f"Conflicting capability exists", agent_id=agent_id)
            return None

        # For dangerous actions, require additional verification
        if action in DANGEROUS_ACTIONS:
            if not await self._verify_dangerous_action(agent_id, action, justification):
                return None

        # Issue capability
        cap = Capability(
            id=str(uuid.uuid4()),
            subject=f"agent:{agent_id}",
            action=action,
            resource=resource,
            constraints=self._derive_constraints(action, resource),
            issued_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            issuer="river",
            signature=""
        )
        cap.signature = self._sign_capability(cap)

        log.info(f"Capability issued", cap_id=cap.id, agent_id=agent_id, action=action)
        return cap

    def _policy_allows(self, action: str, resource: str) -> bool:
        """Check if edition policy allows action."""
        for rule in self.policies:
            if rule.matches(action, resource):
                return rule.effect == "allow"
        return False  # Default deny
```

### Edition Policies

```yaml
# business.yaml
edition: business
rules:
  - action: "*"
    resource: "*"
    effect: allow
    constraints:
      audit_log: required

  - action: "agent:terminate"
    resource: "*"
    effect: deny
    unless: "approval:admin"

# education.yaml
edition: education
rules:
  - action: "tool:execute"
    resource: "tool:web_search"
    effect: allow
    constraints:
      safe_search: enabled
      blocked_domains: [adult, gambling, violence]

  - action: "ledger:write"
    resource: "*"
    effect: deny

# kids.yaml
edition: kids
rules:
  - action: "tool:execute"
    resource: "tool:*"
    effect: allow
    constraints:
      content_filter: strict
      max_response_length: 500

  - action: "memory:*"
    resource: "*"
    effect: deny
    reason: "No persistent memory for kids edition"
```

---

## 5. Secrets Management

### Secret Storage

Secrets are stored encrypted at rest using age encryption.

```python
from pathlib import Path
import age

class SecretStore:
    """Encrypted secrets storage."""

    def __init__(self, store_path: Path, identity_path: Path):
        self.store_path = store_path
        self.identity = age.Identity.from_file(identity_path)

    def get(self, key: str) -> str | None:
        """Retrieve decrypted secret."""
        secret_path = self.store_path / f"{key}.age"
        if not secret_path.exists():
            return None

        encrypted = secret_path.read_bytes()
        return age.decrypt(encrypted, [self.identity]).decode()

    def set(self, key: str, value: str) -> None:
        """Store encrypted secret."""
        recipient = self.identity.to_public()
        encrypted = age.encrypt(value.encode(), [recipient])

        secret_path = self.store_path / f"{key}.age"
        secret_path.write_bytes(encrypted)

    def delete(self, key: str) -> bool:
        """Delete secret."""
        secret_path = self.store_path / f"{key}.age"
        if secret_path.exists():
            secret_path.unlink()
            return True
        return False
```

### Secret Access Control

Secrets require explicit capability grants:

```yaml
# Capability to read specific secret
- id: "cap_secret_001"
  subject: "agent:mumega"
  action: "secret:read"
  resource: "secret:OPENAI_API_KEY"
  constraints:
    audit: true
```

---

## 6.5 Telemetry & Secret Hygiene

- Telemetry is disabled by default (`telemetry_enabled=false` in all editions).
- Secret scanning should run via `sos doctor` to detect accidental key material in repo files.

## 6.6 Runtime Hardening

Use `docker-compose.hardened.yml` as an overlay to enforce:
- `no-new-privileges`
- dropped Linux capabilities
- tmpfs `/tmp`
- read-only root + explicit write paths (when applicable)

## 6. Audit Logging

All security-relevant events MUST be logged to an append-only audit log.

### Audit Events

| Event | Description |
|-------|-------------|
| `capability.requested` | Agent requested capability |
| `capability.issued` | Capability was issued |
| `capability.denied` | Capability request denied |
| `capability.used` | Capability was used |
| `capability.revoked` | Capability was revoked |
| `plugin.loaded` | Plugin was loaded |
| `plugin.blocked` | Plugin load was blocked |
| `secret.accessed` | Secret was accessed |
| `policy.violation` | Policy violation detected |

### Tool Execution Audit
Tools service writes append-only audit logs for every tool call:
- `SOS_HOME/data/audit/tools.jsonl`
- `SOS_HOME/data/ledger/audit.jsonl` (ledger-aligned trail)

Each record includes `timestamp`, `tool`, `ok`, `error`, and `args`.

### Audit Log Format

```json
{
  "ts": "2026-01-10T12:00:00Z",
  "event": "capability.issued",
  "actor": "river",
  "subject": "agent:kasra",
  "action": "memory:read",
  "resource": "memory:agent:kasra/*",
  "capability_id": "cap_001",
  "trace_id": "abc123",
  "outcome": "success"
}
```

---

## 7. Pairing & Allowlists

Pairing is required for DM channels with `dmPolicy=pairing`. The flow:
1) Identity service issues a short-lived pairing code for `(channel, sender_id, agent_id)`.
2) Approver approves the code → allowlist entry is created.
3) A scoped capability is issued for the sender (`tool:execute`, resource `channel:<channel>:sender:<id>`).

Allowlists are stored in the Identity database and exposed via the Identity API.

---

## Implementation Checklist

- [ ] Capability token schema and signing
- [ ] Capability verification middleware
- [ ] Plugin manifest schema and validation
- [ ] Plugin signature verification
- [ ] Subprocess sandboxing
- [ ] Network allowlist enforcement
- [ ] Filesystem isolation
- [ ] River gatekeeper implementation
- [ ] Edition policy engine
- [ ] Secret storage with encryption
- [ ] Audit logging
