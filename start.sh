#!/usr/bin/env bash
# Homelab MCP — process manager
# Usage: start.sh {start|stop|restart|status|logs}
#
# Designed by SK. Built by Claude.

set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LOG="${DIR}/homelab-mcp.log"
PORT="${HOMELAB_MCP_PORT:-1603}"
VENV="${DIR}/venv/bin/python"

[ -f "${DIR}/.env" ] && set -a && source "${DIR}/.env" && set +a

is_running() { pgrep -f "homelab-mcp\.py" >/dev/null 2>&1; }

# ── commands ──────────────────────────────────────────────────────

do_start() {
    if is_running; then
        echo "Homelab MCP already running"
        return 1
    fi
    cd "$DIR"
    SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt \
    REQUESTS_CA_BUNDLE=/etc/ssl/certs/ca-certificates.crt \
    nohup "$VENV" "$DIR/homelab-mcp.py" > "$LOG" 2>&1 &
    echo "Homelab MCP started  PID=$!  :${PORT}"
}

do_stop() {
    is_running || { echo "Homelab MCP is not running"; return 0; }
    pkill -f "homelab-mcp\.py"
    sleep 1
    is_running && pkill -9 -f "homelab-mcp\.py"
    echo "Homelab MCP stopped"
}

do_logs() {
    if systemctl --user is-active --quiet homelab-mcp 2>/dev/null; then
        journalctl --user -u homelab-mcp -f
    else
        tail -f "$LOG"
    fi
}

do_status() {
    if is_running; then
        local pid; pid=$(pgrep -f "homelab-mcp\.py" | head -1)
        echo "RUNNING  PID=$pid  :${PORT}"
        local resp; resp=$(curl -s --max-time 3 "http://localhost:${PORT}/mcp" \
            -X POST -H "Content-Type: application/json" \
            -H "Accept: application/json, text/event-stream" \
            -d '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"check","version":"1.0"}},"id":1}')
        if [ -n "$resp" ]; then
            echo "  Health: OK"
        else
            echo "  Health: unreachable"
        fi
    else
        echo "NOT RUNNING"
    fi
}

# ── main ──────────────────────────────────────────────────────────

case "${1:-start}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; sleep 1; do_start ;;
    status)  do_status ;;
    logs)    do_logs ;;
    *)
        cat <<USAGE
Usage: homelab-mcp {start|stop|restart|status|logs}

  start     Start Homelab MCP (default)
  stop      Stop Homelab MCP
  restart   Restart
  status    Show state and health
  logs      Follow log (journal under systemd, file otherwise)
USAGE
        ;;
esac
