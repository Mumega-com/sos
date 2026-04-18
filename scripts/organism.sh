#!/bin/bash
# organism.sh — Master switch for the SOS organism
#
# Usage:
#   ./organism.sh on        # Start all services + timers
#   ./organism.sh off       # Stop everything, exit all agents
#   ./organism.sh status    # Show what's running
#   ./organism.sh agents    # Start/stop only agent sessions
#
# Every idle Claude/Codex session burns cache tokens on reload.
# Turn OFF when not actively working. Turn ON when ready.

set -e
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus

# Services managed by systemd
SERVICES=(
    agent-lifecycle
    output-capture
    calcifer
    agent-wake-daemon
)

# Timers
TIMERS=(
    trop-daily
    trop-weekly
    trop-monthly
    trop-social
    trop-health
    analytics-ingest
    analytics-decide
    analytics-act
    analytics-feedback
)

# Agent tmux sessions (Claude/Codex — these burn cache tokens when idle)
AGENT_SESSIONS=(
    mkt-lead
    mkt-content
    mkt-outreach
    mkt-analytics
    gaf
)

# Core services (always on — cheap, no LLM tokens)
# sos-mcp-sse, sos-squad, mirror, bus-bridge, sovereign-loop
# These stay running. They're Python/Redis, not LLM.

case "${1:-status}" in
    on|start)
        echo "🟢 ORGANISM ON"
        echo ""
        echo "Starting services..."
        for svc in "${SERVICES[@]}"; do
            systemctl --user start "$svc.service" 2>/dev/null && echo "  ✓ $svc" || echo "  ✗ $svc (failed)"
        done
        echo ""
        echo "Starting timers..."
        for timer in "${TIMERS[@]}"; do
            systemctl --user start "$timer.timer" 2>/dev/null && echo "  ✓ $timer" || echo "  ✗ $timer (failed)"
        done
        echo ""
        echo "Agent sessions NOT auto-started (they burn cache tokens)."
        echo "Start individually: tmux attach -t mkt-lead"
        echo "Or: $0 agents on"
        ;;

    off|stop)
        echo "🔴 ORGANISM OFF"
        echo ""
        echo "Stopping services..."
        for svc in "${SERVICES[@]}"; do
            systemctl --user stop "$svc.service" 2>/dev/null && echo "  ✓ $svc stopped" || echo "  - $svc (already stopped)"
        done
        echo ""
        echo "Stopping timers..."
        for timer in "${TIMERS[@]}"; do
            systemctl --user stop "$timer.timer" 2>/dev/null && echo "  ✓ $timer stopped" || echo "  - $timer (already stopped)"
        done
        echo ""
        echo "Exiting agent sessions..."
        for session in "${AGENT_SESSIONS[@]}"; do
            if tmux has-session -t "$session" 2>/dev/null; then
                tmux send-keys -t "$session" "/exit" Enter 2>/dev/null
                sleep 1
                # Kill tmux session if claude didn't exit
                tmux kill-session -t "$session" 2>/dev/null
                echo "  ✓ $session exited"
            else
                echo "  - $session (not running)"
            fi
        done
        echo ""
        echo "Core services (MCP, Squad, Mirror, bus) still running — no token cost."
        echo "Sovereign loop still running — uses Mirror API, not LLM tokens."
        ;;

    agents)
        case "${2:-status}" in
            on|start)
                echo "Starting agent sessions..."
                for session in "${AGENT_SESSIONS[@]}"; do
                    if ! tmux has-session -t "$session" 2>/dev/null; then
                        tmux new-session -d -s "$session" -c /home/mumega/SOS
                        echo "  ✓ $session created"
                    else
                        echo "  - $session (already exists)"
                    fi
                done
                echo ""
                echo "Sessions created. Start Claude/Codex manually:"
                echo "  tmux attach -t mkt-lead    → claude --dangerously-skip-permissions"
                echo "  tmux attach -t gaf         → claude --dangerously-skip-permissions"
                ;;
            off|stop)
                echo "Stopping agent sessions..."
                for session in "${AGENT_SESSIONS[@]}"; do
                    if tmux has-session -t "$session" 2>/dev/null; then
                        tmux send-keys -t "$session" "/exit" Enter 2>/dev/null
                        sleep 1
                        tmux kill-session -t "$session" 2>/dev/null
                        echo "  ✓ $session stopped"
                    else
                        echo "  - $session (not running)"
                    fi
                done
                ;;
            *)
                echo "Agent sessions:"
                for session in "${AGENT_SESSIONS[@]}"; do
                    if tmux has-session -t "$session" 2>/dev/null; then
                        echo "  🟢 $session"
                    else
                        echo "  ⚫ $session"
                    fi
                done
                ;;
        esac
        ;;

    status)
        echo "═══ ORGANISM STATUS ═══"
        echo ""
        echo "Services:"
        for svc in "${SERVICES[@]}"; do
            state=$(systemctl --user is-active "$svc.service" 2>/dev/null || echo "dead")
            if [ "$state" = "active" ]; then
                echo "  🟢 $svc"
            else
                echo "  ⚫ $svc"
            fi
        done
        echo ""
        echo "Timers:"
        active_timers=0
        for timer in "${TIMERS[@]}"; do
            state=$(systemctl --user is-active "$timer.timer" 2>/dev/null || echo "dead")
            [ "$state" = "active" ] && active_timers=$((active_timers + 1))
        done
        echo "  $active_timers/${#TIMERS[@]} active"
        echo ""
        echo "Agent sessions (burn cache tokens when idle):"
        for session in "${AGENT_SESSIONS[@]}"; do
            if tmux has-session -t "$session" 2>/dev/null; then
                echo "  🟢 $session (⚠ burning cache)"
            else
                echo "  ⚫ $session"
            fi
        done
        echo ""
        echo "Core (always on, no token cost):"
        for core in sos-mcp-sse mirror bus-bridge sovereign-loop; do
            state=$(systemctl --user is-active "$core.service" 2>/dev/null || echo "?")
            if [ "$state" = "active" ]; then
                echo "  🟢 $core"
            else
                echo "  ⚫ $core"
            fi
        done
        echo ""
        # Count running tmux sessions total
        total_tmux=$(tmux list-sessions 2>/dev/null | wc -l)
        echo "Total tmux sessions: $total_tmux"
        ;;

    *)
        echo "Usage: $0 {on|off|status|agents [on|off]}"
        echo ""
        echo "  on      Start services + timers (not agents)"
        echo "  off     Stop everything, exit agents"
        echo "  status  Show what's running"
        echo "  agents  Manage agent sessions separately"
        ;;
esac
