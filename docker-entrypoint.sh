#!/bin/sh
# Runs claude-http-server and mcp-sse-bridge under a simple restart loop.
# If either exits, the whole container exits so Docker restart policy triggers.

restart_on_exit() {
  name=$1; shift
  while true; do
    "$@"
    echo "[entrypoint] $name exited with $? — restarting in 3s" >&2
    sleep 3
  done
}

restart_on_exit claude-http-server node /server/claude-http-server.mjs &
HTTP_PID=$!

# mcp-sse-bridge is foreground — if it dies the container dies
node /bridge/mcp-sse-bridge.mjs 3005 node /server/mcp-shell-server.mjs
echo "[entrypoint] mcp-sse-bridge exited — shutting down" >&2
kill $HTTP_PID 2>/dev/null
exit 1
