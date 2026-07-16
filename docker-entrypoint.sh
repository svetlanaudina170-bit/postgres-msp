#!/bin/bash
set -e

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
