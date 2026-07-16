# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                          # Install dependencies from uv.lock
uv run pytest                    # All tests
uv run pytest tests/unit/ -v --tb=short  # Unit tests only
uv run pytest tests/integration/ -v --tb=short  # Integration tests (needs PostgreSQL)
uv run ruff check .              # Lint
uv run ruff format .             # Auto-format
uv run ruff format --check .     # Check formatting
uv run pyright                   # Type checking
uv build                         # Build wheel and sdist
```

## Run Modes

```bash
python -m src.postgres_mcp.autonomous.app          # Gradio UI
python -m src.postgres_mcp.autonomous.mcp_server   # MCP server (stdio)
python -m src.postgres_mcp.autonomous.mcp_server --transport sse  # MCP server (SSE)
docker-compose up               # Full stack (db + mcp + ui)
just dev                        # Using just runner
```

## Project Architecture

Two main runtime modes controlled by `MCP_MODE` env var: `mcp` (MCP server) or `ui` (Gradio UI).

### Core Package: `src/postgres_mcp/`

**`autonomous/`** — Main application module:
- `mcp_server.py` — FastMCP-based server exposing 7 MCP tools (list_schemas, list_objects, get_object_details, execute_sql, explain_query, analyze_db_health, get_top_queries). Uses FastMCP (`mcp.server.fastmcp`).
- `app.py` (~1893 lines) — Gradio 5.x web UI with tabs: Connection, Chat, SQL Editor, Schema Browser, Health, LLM Settings.
- `pg_client.py` — Async PostgreSQL client using asyncpg with connection pooling.
- `llm_client.py` — Universal LLM client supporting OpenAI, Anthropic, Google APIs.
- `connection_store.py` — Encrypted JSON store for DB connection URLs (Fernet encryption).
- `llm_connection_store.py` — JSON registry for named LLM connections (4-level hierarchy: Mode → Provider → Connection Type → Model).
- `crypto.py` — Fernet (AES-128-CBC + HMAC) encryption utilities.

**`sql/`** — Legacy SQL layer using psycopg3 with connection pooling and SQL validation via pglast AST parsing.

**`database_health/`** — Database health analysis modules (buffer, connections, constraints, indexes, replication, sequences, vacuum).

**`explain/`** — EXPLAIN plan analysis.

**`index/`** — Index optimization engine with DTA (Database Tuning Advisor) and LLM-based optimization.

**`top_queries/`** — Top SQL queries analysis (requires pg_stat_statements).

### Conventions

- Python 3.12+
- Package manager: `uv` (with `uv.lock` lockfile)
- Build system: Hatchling
- Docstrings: Google-style (`ruff.lint.pydocstyle.convention = "google"`)
- Formatting: Line length 150, double quotes, space indentation
- Linting: Ruff (rules E, F, I, B, W, N, UP, RUF)
- Type checking: Pyright (standard mode)
- Every source file has a `VERSION: x.x.x` header
- Tests use `pytest` + `pytest-asyncio`, optionally using Docker for real PostgreSQL instances via `tests/conftest.py`

## SQL Editor Package (`src/postgres_mcp/sql_editor/`)

New package providing a full-featured SQL constructor:

- `builder.py` — `SQLBuilder` chainable builder (SELECT, INSERT, UPDATE, DELETE, CREATE, MERGE, CTE, etc.) with columns, WHERE, JOIN, ORDER BY, GROUP BY, LIMIT support
- `templates.py` — 16 SQL templates/snippets (Row count, Table size, Indexes, FK, Bloat, Locks, etc.)
- `history.py` — `QueryHistory` persistent JSON-backed query history with favorites

## Gradio UI SQL Editor Tab

Rewritten with:
- **Statement type selector** (12 types: SELECT, INSERT, UPDATE, DELETE, CREATE TABLE, EXPLAIN, CTE, MERGE, etc.)
- **Object palette** (Schema → Table → Columns dropdowns populated live from DB)
- **Builder form** that changes dynamically based on statement type: WHERE, ORDER BY, GROUP BY, HAVING, LIMIT, JOIN builder, DISTINCT
- **SQL Preview** (auto-generated from form) + **Editable SQL** textarea
- **Format** button (via sqlparse)
- **Quick Templates** dropdown (16 templates)
- **Export** to CSV/JSON
- **Query History** with restore

## MCP Server Tools (13 total)

| Tool | Purpose |
|---|---|
| `list_schemas` | List DB schemas |
| `list_objects` | Tables/views/sequences in schema |
| `get_object_details` | Column details for table/view |
| `execute_sql` | Run any SQL |
| `explain_query` | Execution plan (EXPLAIN) |
| `analyze_db_health` | Database health report |
| `get_top_queries` | Top queries (pg_stat_statements) |
| `analyze_index_performance` | Index usage analysis |
| `get_active_queries` | Currently running queries |
| `get_table_sizes` | Table/index/total sizes |
| `get_database_locks` | Blocking locks detection |
| `format_sql_query` | SQL formatting (sqlparse) |
| `get_database_info` | DB version, size, extensions, uptime |
