#!/usr/bin/env bash
# tokens-audit.sh — deterministic check that every consumer of tokens.json
# uses the post-SEC-001 hash-aware verifier pattern (sha256 or bcrypt), not
# the `entry["token"] == token` anti-pattern.
#
# Zero tokens. Run on any suspected auth regression.

set -uo pipefail
cd /mnt/HC_Volume_104325311/SOS

echo "=== files that load tokens.json ==="
grep -rlE "tokens\.json|bus/tokens" --include='*.py' sos/ /home/mumega/mirror/ 2>/dev/null | sort -u

echo
echo "=== suspicious anti-pattern occurrences ==="
grep -rnE 'entry\.get\("token"\)\s*==|entry\["token"\]\s*==|x\.get\("token"\)\s*==' \
    --include='*.py' sos/ /home/mumega/mirror/ 2>/dev/null || echo "  (none found — clean)"

echo
echo "=== hash-aware verifiers (known-good pattern) ==="
grep -rnE 'token_hash|hashlib\.sha256\(token' --include='*.py' sos/ /home/mumega/mirror/ 2>/dev/null | head -20

echo
echo "=== raw-field population in tokens.json ==="
python3 -c "
import json
t = json.load(open('sos/bus/tokens.json'))
with_raw = sum(1 for x in t if x.get('token'))
with_hash = sum(1 for x in t if x.get('token_hash') or x.get('hash'))
print(f'  total entries:       {len(t)}')
print(f'  with raw \"token\":    {with_raw}  (should be 0 post-SEC-001)')
print(f'  with hash field:     {with_hash}  (should equal total)')
print(f'  active:              {sum(1 for x in t if x.get(\"active\"))}')
"
