#!/usr/bin/env bash
# check-all-pipes.sh — deterministic pipe-health probe for sos-medic.
# Output: JSON, one object per pipe with {pipe, status, detail, latency_ms}.
# Zero LLM tokens. Run this FIRST on any incident before reasoning.
#
# Usage:   check-all-pipes.sh [known-good-customer-token]
# Example: check-all-pipes.sh sk-trop-f3ad1fb355a2d2ffa408bdcb4182d50e

set -uo pipefail
TOKEN="${1:-}"

probe() {
    local pipe="$1" url="$2" expected="$3" auth_header="${4:-}"
    local start_ms status body latency
    start_ms=$(date +%s%3N)
    if [[ -n "$auth_header" ]]; then
        status=$(curl -sS -o /tmp/medic-body -w '%{http_code}' --max-time 5 -H "$auth_header" "$url" 2>/dev/null || echo "000")
    else
        status=$(curl -sS -o /tmp/medic-body -w '%{http_code}' --max-time 5 "$url" 2>/dev/null || echo "000")
    fi
    latency=$(( $(date +%s%3N) - start_ms ))
    body=$(head -c 160 /tmp/medic-body 2>/dev/null | tr '\n' ' ')
    local result="fail"
    if [[ "$status" == "$expected" ]]; then
        result="pass"
    fi
    printf '  {"pipe":"%s","status":"%s","http":%s,"latency_ms":%d,"detail":"%s"}' \
           "$pipe" "$result" "$status" "$latency" "${body//\"/\\\"}"
}

echo "["
probe "bus-gateway :6070"    "http://localhost:6070/health"  "200"
echo ","
probe "squad :8060"          "http://localhost:8060/health"  "200"
echo ","
probe "saas-registry :8075"  "http://localhost:8075/health"  "200"
echo ","
probe "dashboard :8090"      "http://localhost:8090/health"  "200"
echo ","
probe "mirror :8844"         "http://localhost:8844/"        "200"
echo ","
probe "nginx:app.mumega"     "https://app.mumega.com/login"  "200"
echo ","
probe "nginx:mcp.mumega"     "https://mcp.mumega.com/"       "200"

if [[ -n "$TOKEN" ]]; then
    echo ","
    probe "dashboard-auth"   "http://localhost:8090/api/status" "401"
    echo ","
    # With cookie, status should be 200. We issue the login first (ignores status) then probe.
    curl -sS -X POST http://localhost:8090/login -d "token=$TOKEN" -c /tmp/medic-cookie -o /dev/null 2>/dev/null
    start_ms=$(date +%s%3N)
    status=$(curl -sS -o /tmp/medic-body -w '%{http_code}' --max-time 5 -b /tmp/medic-cookie http://localhost:8090/api/status 2>/dev/null || echo "000")
    latency=$(( $(date +%s%3N) - start_ms ))
    result="fail"; [[ "$status" == "200" ]] && result="pass"
    printf '  {"pipe":"dashboard-logged-in","status":"%s","http":%s,"latency_ms":%d,"detail":"%s"}' \
           "$result" "$status" "$latency" "$(head -c 120 /tmp/medic-body | tr '\n' ' ' | sed 's/"/\\"/g')"
    echo ","
    probe "mirror-customer-auth" "http://localhost:8844/recent/${TOKEN##*-}?limit=1" "200" "Authorization: Bearer $TOKEN"
fi

echo ""
echo "]"
rm -f /tmp/medic-body /tmp/medic-cookie
