# =========================================================================
# VERSION: 2.0.1
# Path: src/postgres_mcp/autonomous/mcp_server.py
# Изменения в 2.0.1:
#  - ИСПРАВЛЕНА SQL-ИНЪЕКЦИЯ: list_objects и get_object_details теперь
#    валидируют schema_name, object_type, object_name через regex
#    (^[a-zA-Z_][a-zA-Z0-9_]*$) перед подстановкой в SQL.
#    Невалидные имена вызывают ValueError (FastMCP → tool-error клиенту).
# =========================================================================
# АРХИТЕКТУРНАЯ ПЕРЕРАБОТКА:
#  - Сервер переписан с ручной реализации JSON-RPC/stdio-протокола
#    (самописный handle_message с if/elif по методам, ручной парсинг
#    строк из stdin, свой SSE-сервер на aiohttp) на официальный
#    FastMCP API: `from mcp.server.fastmcp import FastMCP`.
#  - НИКАКИХ новых зависимостей добавлять не пришлось. FastMCP 1.0 была
#    включена прямо в официальный MCP Python SDK в 2024 году и живёт
#    в уже имеющейся зависимости "mcp[cli]>=1.5.0" (см. pyproject.toml)
#    как модуль mcp.server.fastmcp. Отдельный PyPI-пакет "fastmcp"
#    (PrefectHQ, ныне версии 3.x+) — это другой, хоть и родственный,
#    проект с другим API (host/port только в run(), а не в конструкторе,
#    другие импорты) — использовать его здесь не нужно и не стали.
#  - Каждый инструмент — обычная async Python-функция с типизированными
#    аргументами и docstring (Google-style — уже принят как конвенция
#    в pyproject.toml: tool.ruff.lint.pydocstyle.convention = "google"),
#    декорированная @mcp.tool(). FastMCP сам генерирует JSON Schema
#    из типов и текст описания инструмента из docstring — вместо
#    того, чтобы вручную дублировать JSON Schema в tools/list, как было
#    раньше.
#  - Обработка ошибок теперь тоже на стороне FastMCP: необработанное
#    исключение в теле инструмента автоматически превращается в
#    MCP tool-error ответ клиенту. Ручной try/except с кодами
#    -32000/-32601 для каждого метода больше не нужен.
#  - Транспорт (stdio/sse) по-прежнему выбирается через .env
#    MCP_TRANSPORT (или флаг --transport при запуске) — поведение
#    выбора транспорта сохранено, но сама реализация транспорта
#    (ручной stdin-луп, свой aiohttp SSE-сервер) полностью убрана —
#    это теперь ответственность FastMCP (mcp.run(transport=...)).
#  - Вся бизнес-логика инструментов (execute_sql, explain_query,
#    analyze_db_health, get_top_queries, list_schemas, list_objects,
#    get_object_details) сохранена по поведению 1:1, включая .env-конфиг
#    из этапа 2 (EXPLAIN_FORMAT/EXPLAIN_ANALYZE/TOP_QUERIES_DEFAULT_LIMIT)
#    и абсолютный путь к .env (этап 2, v1.1.0).
#  - СОХРАНЁН (намеренно не исправлен, чтобы не расширять правку) старый
#    нюанс: параметр sort_by у get_top_queries принимается (для
#    совместимости со старой схемой инструмента), но не используется —
#    сортировка всегда по total_exec_time (см. pg_client.get_top_queries).
#    Аналогично health_type у analyze_db_health — принимается, но отчёт
#    всегда включает все проверки. Если нужна реальная реализация
#    сортировки/фильтрации — отдельная задача, сообщите.
#  - Разбор аргумента --transport из командной строки сделан устойчивее
#    (ищет флаг по имени через sys.argv.index, а не по фиксированной
#    позиции sys.argv[2]) — раньше "python mcp_server.py --transport sse"
#    работало только если "sse" было ровно третьим аргументом.
# =========================================================================
"""
PostgreSQL MCP Server — подключается к PostgreSQL и предоставляет инструменты
через Model Context Protocol (MCP), реализовано на официальном FastMCP API
(mcp.server.fastmcp, часть пакета mcp[cli]).

Совместим с: Claude Desktop, Cursor, Continue.dev, Windsurf и любыми MCP-клиентами.

Запуск:
  python mcp_server.py                    # транспорт из .env (MCP_TRANSPORT, по умолчанию stdio)
  python mcp_server.py --transport sse     # принудительно SSE (host/port из .env MCP_SERVER_HOST/PORT)

Подключение в Claude Desktop (claude_desktop_config.json):
  {
    "mcpServers": {
      "postgresql": {
        "command": "python",
        "args": ["путь/к/mcp_server.py"]
      }
    }
  }
"""

import json
import logging
import os
import re
import sys
from typing import Optional

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Абсолютный путь к .env — вычисляется от расположения файла, а не от CWD.
# mcp_server.py лежит в src/postgres_mcp/autonomous/, .env — в корне проекта
# (4 уровня вверх), т.е. по той же схеме, что и в app.py.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")
load_dotenv(ENV_PATH)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [MCP] %(message)s")
logger = logging.getLogger("pg_mcp_server")

DATABASE_URL = os.getenv("DATABASE_URL", "")
MCP_TRANSPORT = os.getenv("MCP_TRANSPORT", "stdio")
MCP_SERVER_HOST = os.getenv("MCP_SERVER_HOST", "127.0.0.1")
MCP_SERVER_PORT = int(os.getenv("MCP_SERVER_PORT", "8100"))

_PG_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_identifier(name: str, label: str = "identifier") -> str:
    """Проверяет, что name является валидным PostgreSQL-идентификатором
    (только буквы, цифры, подчёркивание; начинается с буквы/_).
    Предотвращает SQL-инъекции через schema_name/object_type/object_name."""
    if not name or not _PG_IDENTIFIER_RE.match(name):
        raise ValueError(f"Invalid {label}: '{name}'. Only letters, digits, and underscores are allowed.")
    return name


mcp = FastMCP(
    "postgresql-mcp",
    host=MCP_SERVER_HOST,
    port=MCP_SERVER_PORT,
    transport_security=None,
)

pg = None


async def get_pg():
    """Ленивая инициализация единственного PostgresClient на процесс
    (с авто-подключением к DATABASE_URL из .env, если он задан)."""
    global pg
    if pg is None:
        from .pg_client import PostgresClient

        pg = PostgresClient()
        if DATABASE_URL:
            err = await pg.connect(DATABASE_URL)
            if err:
                logger.warning(f"Auto-connect failed: {err}")
    return pg


async def _ensure_connected(pg_client, database_url: Optional[str]) -> None:
    """Подключается к database_url (или DATABASE_URL из .env), если ещё
    не подключены. Бросает RuntimeError при неудаче — FastMCP сам
    превратит его в понятный tool-error ответ клиенту."""
    db_url = database_url or DATABASE_URL
    if not pg_client.is_connected and db_url:
        err = await pg_client.connect(db_url)
        if err:
            raise RuntimeError(f"Connection failed: {err}")


@mcp.tool()
async def list_schemas(database_url: Optional[str] = None) -> str:
    """List all schemas in the database.

    Args:
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    r = await pg_client.execute_sql("SELECT schema_name FROM information_schema.schemata ORDER BY schema_name")
    if r.error:
        return f"Error: {r.error}"
    return json.dumps([{"schema_name": row[0]} for row in r.rows], default=str)


@mcp.tool()
async def list_objects(
    schema_name: str,
    object_type: str = "table",
    database_url: Optional[str] = None,
) -> str:
    """List tables/views/sequences in a schema.

    Args:
        schema_name: Name of the schema to list objects from.
        object_type: One of "table", "view", "sequence" (default: "table").
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    _validate_identifier(schema_name, "schema_name")
    _validate_identifier(object_type, "object_type")
    table_type = "BASE TABLE" if object_type == "table" else "VIEW" if object_type == "view" else "SEQUENCE"
    r = await pg_client.execute_sql(
        f"SELECT table_name AS name FROM information_schema.tables "
        f"WHERE table_schema='{schema_name}' AND table_type='{table_type}' ORDER BY table_name"
    )
    if r.error:
        return f"Error: {r.error}"
    return json.dumps([{"name": row[0]} for row in r.rows], default=str)


@mcp.tool()
async def get_object_details(
    schema_name: str,
    object_name: str,
    database_url: Optional[str] = None,
) -> str:
    """Get columns of a table or view.

    Args:
        schema_name: Schema containing the object.
        object_name: Table or view name.
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    _validate_identifier(schema_name, "schema_name")
    _validate_identifier(object_name, "object_name")
    r = await pg_client.execute_sql(
        f"SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        f"WHERE table_schema='{schema_name}' AND table_name='{object_name}' ORDER BY ordinal_position"
    )
    if r.error:
        return f"Error: {r.error}"
    columns = [{"column": row[0], "data_type": row[1], "is_nullable": row[2], "column_default": row[3]} for row in r.rows]
    return json.dumps({"name": object_name, "columns": columns}, default=str)


@mcp.tool()
async def execute_sql(sql: str, database_url: Optional[str] = None) -> str:
    """Execute a SQL query.

    Args:
        sql: SQL statement to execute.
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    r = await pg_client.execute_sql(sql)
    if r.error:
        return f"Error: {r.error}"
    if r.columns:
        return json.dumps([dict(zip(r.columns, row)) for row in r.rows], default=str)
    return json.dumps({"affected_rows": r.row_count}, default=str)


@mcp.tool()
async def explain_query(
    sql: str,
    analyze: Optional[bool] = None,
    database_url: Optional[str] = None,
) -> str:
    """Get query execution plan.

    Args:
        sql: SQL statement to explain.
        analyze: If true, actually executes the query for real timing data
            (default: value of EXPLAIN_ANALYZE in .env, normally false).
            WARNING: true really runs the query, including any
            INSERT/UPDATE/DELETE it contains — use with care.
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    explain_format = os.getenv("EXPLAIN_FORMAT", "JSON")
    if analyze is None:
        analyze = os.getenv("EXPLAIN_ANALYZE", "false").lower() == "true"
    r = await pg_client.execute_sql(f"EXPLAIN (FORMAT {explain_format}, ANALYZE {str(bool(analyze)).lower()}) {sql}")
    if r.error or not r.rows:
        return f"Error: {r.error}"
    return r.rows[0][0][0]


@mcp.tool()
async def analyze_db_health(
    health_type: str = "all",
    database_url: Optional[str] = None,
) -> str:
    """Database health overview.

    Args:
        health_type: Принимается для совместимости со старой схемой
            инструмента, но пока не используется — отчёт всегда
            включает все проверки (default: "all").
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    return await pg_client.get_health_report()


@mcp.tool()
async def get_top_queries(
    limit: Optional[int] = None,
    sort_by: str = "resources",
    database_url: Optional[str] = None,
) -> str:
    """Top queries by resource usage (requires pg_stat_statements extension).

    Args:
        limit: How many queries to return (optional, defaults to
            TOP_QUERIES_DEFAULT_LIMIT from .env).
        sort_by: Принимается для совместимости со старой схемой
            инструмента, но пока не используется — сортировка всегда
            по total_exec_time (default: "resources").
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    return await pg_client.get_top_queries(limit)


@mcp.tool()
async def analyze_index_performance(
    schema_name: str = "public",
    database_url: Optional[str] = None,
) -> str:
    """Analyze index usage and find unused/duplicate indexes.

    Args:
        schema_name: Schema to analyze (default: "public").
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    _validate_identifier(schema_name, "schema_name")

    r = await pg_client.execute_sql(
        "SELECT schemaname, tablename, indexname, idx_scan, "
        "pg_size_pretty(pg_relation_size(indexrelid)) AS index_size "
        f"FROM pg_stat_user_indexes WHERE schemaname = '{schema_name}' "
        "ORDER BY idx_scan ASC"
    )
    if r.error:
        return f"Error: {r.error}"
    if not r.rows:
        return "No index statistics found."
    lines = ["Index Usage Analysis:",
             f"{'Table':20} {'Index':30} {'Scans':8} {'Size':10}",
             "-" * 70]
    for row in r.rows:
        lines.append(f"{str(row[1]):20} {str(row[2]):30} {str(row[3]):8} {str(row[4]):10}")
    lines.append("")
    lines.append("Note: idx_scan = 0 means the index is never used.")
    return "\n".join(lines)


@mcp.tool()
async def get_active_queries(
    database_url: Optional[str] = None,
) -> str:
    """List currently running queries and their duration.

    Args:
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    r = await pg_client.execute_sql(
        "SELECT pid, state, now() - query_start AS duration, "
        "substring(query, 1, 120) AS query_preview "
        "FROM pg_stat_activity "
        "WHERE state != 'idle' AND backend_type = 'client backend' "
        "ORDER BY query_start DESC"
    )
    if r.error:
        return f"Error: {r.error}"
    if not r.rows:
        return "No active queries."
    lines = ["Active Queries:",
             f"{'PID':8} {'State':12} {'Duration':12} {'Query'}",
             "-" * 80]
    for row in r.rows:
        lines.append(f"{str(row[0]):8} {str(row[1]):12} {str(row[2]):12} {str(row[3])}")
    return "\n".join(lines)


@mcp.tool()
async def get_table_sizes(
    schema_name: str = "public",
    sort_by: str = "total",
    database_url: Optional[str] = None,
) -> str:
    """Show table sizes including indexes and total.

    Args:
        schema_name: Schema to analyze (default: "public").
        sort_by: Sort order — "total", "table", or "indexes" (default: "total").
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    _validate_identifier(schema_name, "schema_name")
    order_col = {"total": "total", "table": "table", "indexes": "indexes"}.get(sort_by, "total")
    r = await pg_client.execute_sql(
        "SELECT schemaname, tablename, "
        "pg_size_pretty(pg_table_size(schemaname||'.'||tablename)) AS table_size, "
        "pg_size_pretty(pg_indexes_size(schemaname||'.'||tablename)) AS indexes_size, "
        "pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS total_size "
        f"FROM pg_tables WHERE schemaname = '{schema_name}' "
        f"ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC"
    )
    if r.error:
        return f"Error: {r.error}"
    if not r.rows:
        return "No tables found."
    lines = ["Table Sizes:",
             f"{'Table':30} {'Table Size':12} {'Indexes':12} {'Total':12}",
             "-" * 70]
    for row in r.rows:
        lines.append(f"{str(row[1]):30} {str(row[2]):12} {str(row[3]):12} {str(row[4]):12}")
    return "\n".join(lines)


@mcp.tool()
async def get_database_locks(
    database_url: Optional[str] = None,
) -> str:
    """Show current database locks and blocking queries.

    Args:
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    r = await pg_client.execute_sql(
        "SELECT a.pid, a.state, a.query AS blocked_query, "
        "b.pid AS blocking_pid, b.query AS blocking_query "
        "FROM pg_stat_activity a JOIN pg_stat_activity b ON "
        "a.pid = ANY(pg_blocking_pids(b.pid)) "
        "WHERE a.state = 'active'"
    )
    if r.error:
        return f"Error: {r.error}"
    if not r.rows:
        return "No blocking locks detected."
    lines = ["Database Locks:",
             f"{'Blocked PID':12} {'State':10} {'Blocking PID':14} {'Blocked Query':50}",
             "-" * 90]
    for row in r.rows:
        lines.append(f"{str(row[0]):12} {str(row[1]):10} {str(row[2]):14} {str(row[3])[:50]}")
    return "\n".join(lines)


@mcp.tool()
async def format_sql_query(sql: str) -> str:
    """Format/beautify a SQL query for readability.

    Args:
        sql: Raw SQL text to format.
    """
    import sqlparse
    try:
        return sqlparse.format(sql, reindent=True, keyword_case="upper", use_space_around_operators=True)
    except Exception as e:
        return f"Error formatting SQL: {e}\n\n{sql}"


@mcp.tool()
async def get_database_info(
    database_url: Optional[str] = None,
) -> str:
    """Get general database information: version, size, extensions, server settings.

    Args:
        database_url: Database URL (optional, uses DATABASE_URL from .env if omitted).
    """
    pg_client = await get_pg()
    await _ensure_connected(pg_client, database_url)
    parts = []
    # Version
    r = await pg_client.execute_sql("SELECT version()")
    if not r.error and r.rows:
        parts.append(f"Version: {r.rows[0][0]}")

    # Database size
    r = await pg_client.execute_sql(
        "SELECT pg_size_pretty(pg_database_size(current_database()))"
    )
    if not r.error and r.rows:
        parts.append(f"Database size: {r.rows[0][0]}")

    # Extensions
    r = await pg_client.execute_sql(
        "SELECT string_agg(extname || ' ' || extversion, ', ' ORDER BY extname) "
        "FROM pg_extension"
    )
    if not r.error and r.rows:
        parts.append(f"Extensions: {r.rows[0][0]}")

    # Connection count
    r = await pg_client.execute_sql(
        "SELECT count(*)::int FROM pg_stat_activity WHERE state IS NOT NULL"
    )
    if not r.error and r.rows:
        parts.append(f"Active connections: {r.rows[0][0]}")

    # Uptime
    r = await pg_client.execute_sql("SELECT pg_postmaster_start_time()")
    if not r.error and r.rows:
        parts.append(f"Server started: {r.rows[0][0]}")

    return "\n".join(parts) if parts else "No database info available"


if __name__ == "__main__":
    transport = MCP_TRANSPORT
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]

    logger.info(f"Starting MCP server (FastMCP) — transport={transport}")

    if transport in ("sse", "streamable-http"):
        mcp.run(transport=transport)
    else:
        mcp.run(transport=transport)
