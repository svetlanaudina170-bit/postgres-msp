#!/bin/bash
set -e

# ---------------------------------------------------------------------------
# Docker host fallback — если DATABASE_URL содержит localhost, заменяем его на
# хост Docker (host.docker.internal на Docker Desktop, 172.17.0.1 на Linux).
# Это позволяет контейнеру подключаться к PostgreSQL на хостовой машине.
# ---------------------------------------------------------------------------
_replace_localhost() {
  local url="$1"
  local host=""
  if ping -c1 -W1 host.docker.internal >/dev/null 2>&1; then
    host="host.docker.internal"
  elif ping -c1 -W1 172.17.0.1 >/dev/null 2>&1; then
    host="172.17.0.1"
  fi
  if [ -n "$host" ]; then
    echo "${url//localhost/$host}"
    return 0
  fi
  echo "$url"
  return 1
}

if [ -n "$DATABASE_URL" ] && [[ "$DATABASE_URL" == *"://localhost"* ]]; then
  new_url=$(_replace_localhost "$DATABASE_URL")
  if [ "$new_url" != "$DATABASE_URL" ]; then
    echo "  DATABASE_URL: localhost -> ${new_url%%@*}" >&2
    export DATABASE_URL="$new_url"
  fi
fi

echo "==============================="
echo "  Postgres MCP — starting"
echo "  MODE: ${MCP_MODE:-mcp}"
echo "==============================="

case "${MCP_MODE:-mcp}" in
  mcp)
    exec python -m postgres_mcp.autonomous.mcp_server "$@"
    ;;
  ui)
    exec python -m postgres_mcp.autonomous.app "$@"
    ;;
  server)
    exec python -m postgres_mcp.server "$@"
    ;;
  *)
    echo "Unknown MCP_MODE: ${MCP_MODE}"
    echo "Valid modes: mcp, ui, server"
    exit 1
    ;;
esac
