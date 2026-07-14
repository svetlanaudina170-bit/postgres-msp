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


mcp = FastMCP("postgresql-mcp")

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


if __name__ == "__main__":
    transport = MCP_TRANSPORT
    if "--transport" in sys.argv:
        idx = sys.argv.index("--transport")
        if idx + 1 < len(sys.argv):
            transport = sys.argv[idx + 1]

    logger.info(f"Starting MCP server (FastMCP) — transport={transport}")

    if transport in ("sse", "streamable-http"):
        mcp.run(transport=transport, host=MCP_SERVER_HOST, port=MCP_SERVER_PORT)
    else:
        mcp.run(transport=transport)
