# =========================================================================
# VERSION: 1.1.0
# Path: src/postgres_mcp/autonomous/pg_client.py
# Изменения:
#  - Добавлен import os
#  - Параметры пула соединений (min_size/max_size/timeout/command_timeout)
#    теперь читаются из .env (DB_POOL_MIN_SIZE, DB_POOL_MAX_SIZE,
#    DB_POOL_TIMEOUT, DB_POOL_COMMAND_TIMEOUT) с fallback на прежние
#    значения (1/5/10/30), если переменные не заданы.
#  - get_schema(): лимит схем (было захардкожено [:10]) вынесен
#    в SCHEMA_MAX_SCHEMAS.
#  - explain_query(): формат/режим EXPLAIN вынесены в EXPLAIN_FORMAT
#    и EXPLAIN_ANALYZE.
#  - get_top_queries(): лимит и паттерн исключения вынесены
#    в TOP_QUERIES_DEFAULT_LIMIT и TOP_QUERIES_EXCLUDE_PATTERN.
#  - ПОПУТНО ИСПРАВЛЕН существовавший баг: запрос top_queries использовал
#    плейсхолдер "LIMIT $1", но execute_sql() не поддерживает параметры
#    (и переданный аргумент limit нигде не подставлялся) — запрос
#    реально упал бы с ошибкой asyncpg при вызове. Теперь limit
#    подставляется напрямую в текст SQL через int() (безопасно,
#    т.к. int() отбрасывает всё, что не является целым числом).
#    Если это нежелательное изменение поведения — сообщите, откачу.
# =========================================================================

import logging
import os
import socket
from dataclasses import dataclass
from dataclasses import field
from typing import Optional
from urllib.parse import urlparse

import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Docker host detection — внутри контейнера localhost не работает для
# подключения к хостовой БД. Заменяем на host.docker.internal (Docker
# Desktop) или 172.17.0.1 (Linux bridge), если они доступны.
# ---------------------------------------------------------------------------
_DOCKER_HOST_CACHE: Optional[str] = None


def _in_docker() -> bool:
    """Проверить, запущены ли мы внутри Docker-контейнера."""
    if os.path.exists("/.dockerenv"):
        return True
    try:
        with open("/proc/1/cgroup") as f:
            return "docker" in f.read() or "containerd" in f.read()
    except (FileNotFoundError, OSError):
        pass
    return False


def _detect_docker_host() -> Optional[str]:
    """Определить IP/имя хоста Docker для замены localhost в URL БД."""
    global _DOCKER_HOST_CACHE
    if _DOCKER_HOST_CACHE is not None:
        return _DOCKER_HOST_CACHE
    if not _in_docker():
        _DOCKER_HOST_CACHE = ""
        return None
    # Проверяем DNS: host.docker.internal (Docker Desktop / WSL2)
    try:
        socket.gethostbyname("host.docker.internal")
        _DOCKER_HOST_CACHE = "host.docker.internal"
        return _DOCKER_HOST_CACHE
    except OSError:
        pass
    # Fallback: стандартный bridge Docker
    _DOCKER_HOST_CACHE = "172.17.0.1"
    return _DOCKER_HOST_CACHE


def _maybe_replace_localhost(url: str) -> str:
    """Если URL содержит localhost и мы в Docker — заменить на хост Docker."""
    if "://localhost" not in url and "://localhost:" not in url:
        return url
    host = _detect_docker_host()
    if not host:
        return url
    replaced = url.replace("://localhost", f"://{host}")
    logger.info("Docker container detected: replaced localhost with %s in DATABASE_URL", host)
    return replaced


@dataclass
class SqlResult:
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    row_count: int = 0
    duration_ms: float = 0
    error: Optional[str] = None


@dataclass
class SchemaInfo:
    schemas: list[dict] = field(default_factory=list)
    tables: list[dict] = field(default_factory=list)
    columns: list[dict] = field(default_factory=list)


class PostgresClient:
    """Прямое подключение к PostgreSQL через asyncpg."""

    def __init__(self):
        self._pool: Optional[asyncpg.Pool] = None
        self._current_url: Optional[str] = None

    @property
    def is_connected(self) -> bool:
        return self._pool is not None and not self._pool._closed

    @property
    def current_url(self) -> Optional[str]:
        return self._current_url

    def _parse_url(self, url: str) -> dict:
        parsed = urlparse(url)
        return {
            "host": parsed.hostname or "localhost",
            "port": parsed.port or 5432,
            "user": parsed.username or "postgres",
            "password": parsed.password or "",
            "database": parsed.path.lstrip("/") or "postgres",
        }

    async def connect(self, url: str) -> Optional[str]:
        url = _maybe_replace_localhost(url)
        try:
            if self.is_connected:
                await self.disconnect()
            cfg = self._parse_url(url)
            # Параметры пула читаются здесь (внутри метода), а не на
            # уровне модуля, чтобы гарантированно подхватить .env,
            # даже если connect() вызван до load_dotenv() в app.py.
            pool_min = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
            pool_max = int(os.getenv("DB_POOL_MAX_SIZE", "5"))
            pool_timeout = int(os.getenv("DB_POOL_TIMEOUT", "10"))
            pool_cmd_timeout = int(os.getenv("DB_POOL_COMMAND_TIMEOUT", "30"))
            self._pool = await asyncpg.create_pool(
                host=cfg["host"],
                port=cfg["port"],
                user=cfg["user"],
                password=cfg["password"],
                database=cfg["database"],
                min_size=pool_min,
                max_size=pool_max,
                timeout=pool_timeout,
                command_timeout=pool_cmd_timeout,
            )
            self._current_url = url
            return None
        except Exception as e:
            self._pool = None
            self._current_url = None
            return str(e)

    async def connect_raw(self, host, port, user, password, database) -> Optional[str]:
        return await self.connect(f"postgresql://{user}:{password}@{host}:{port}/{database}")

    async def disconnect(self):
        if self._pool:
            try:
                await self._pool.close()
            except (RuntimeError, AttributeError):
                # Event loop may be closed (Gradio recycles loops on restart).
                # Fall back to synchronous terminate() which doesn't need the loop.
                try:
                    self._pool.terminate()
                except Exception:
                    pass
            self._pool = None
            self._current_url = None

    async def execute_sql(self, sql: str) -> SqlResult:
        if not self.is_connected:
            return SqlResult(error="Not connected")
        import time

        start = time.monotonic()
        try:
            async with self._pool.acquire() as conn:
                stripped = sql.strip().upper()
                if (
                    stripped.startswith("SELECT")
                    or stripped.startswith("WITH")
                    or stripped.startswith("EXPLAIN")
                    or stripped.startswith("SHOW")
                    or stripped.startswith("DESCRIBE")
                ):
                    rows = await conn.fetch(sql)
                    duration = (time.monotonic() - start) * 1000
                    if not rows:
                        return SqlResult(row_count=0, duration_ms=duration)
                    columns = list(rows[0].keys())
                    data = [[row[col] for col in columns] for row in rows]
                    return SqlResult(columns=columns, rows=data, row_count=len(data), duration_ms=duration)
                else:
                    result = await conn.execute(sql)
                    duration = (time.monotonic() - start) * 1000
                    return SqlResult(row_count=result if isinstance(result, int) else 0, duration_ms=duration)
        except Exception as e:
            duration = (time.monotonic() - start) * 1000
            return SqlResult(error=str(e), duration_ms=duration)

    async def get_databases(self, host_url: str) -> list[dict]:
        temp = PostgresClient()
        err = await temp.connect(host_url)
        if err:
            return [{"error": err}]
        result = await temp.execute_sql("SELECT datname FROM pg_database WHERE datistemplate = false ORDER BY datname")
        await temp.disconnect()
        if result.error:
            return [{"error": result.error}]
        return [{"name": row[0]} for row in result.rows if row]

    async def get_schema(self) -> SchemaInfo:
        if not self.is_connected:
            return SchemaInfo()
        info = SchemaInfo()
        sr = await self.execute_sql(
            "SELECT schema_name FROM information_schema.schemata "
            "WHERE schema_name NOT LIKE 'pg_%' AND schema_name != 'information_schema' ORDER BY schema_name"
        )
        if not sr.error:
            info.schemas = [{"name": row[0]} for row in sr.rows]
        max_schemas = int(os.getenv("SCHEMA_MAX_SCHEMAS", "10"))
        for schema in info.schemas[:max_schemas]:
            tr = await self.execute_sql(
                f"SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = '{schema['name']}' AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
            if not tr.error:
                for row in tr.rows:
                    tname = row[0]
                    info.tables.append({"schema": schema["name"], "name": tname})
                    cr = await self.execute_sql(
                        f"SELECT column_name, data_type, is_nullable, column_default "
                        f"FROM information_schema.columns "
                        f"WHERE table_schema = '{schema['name']}' AND table_name = '{tname}' ORDER BY ordinal_position"
                    )
                    if not cr.error:
                        for crow in cr.rows:
                            info.columns.append(
                                {
                                    "schema": schema["name"],
                                    "table": tname,
                                    "name": crow[0],
                                    "type": crow[1],
                                    "nullable": crow[2] == "YES",
                                    "default": crow[3],
                                }
                            )
        return info

    async def get_schema_text(self) -> str:
        info = await self.get_schema()
        lines = []
        for t in info.tables:
            cols = [c for c in info.columns if c["schema"] == t["schema"] and c["table"] == t["name"]]
            lines.append(f"TABLE {t['schema']}.{t['name']}:")
            for c in cols:
                lines.append(f"  {c['name']} {c['type']}" + (" NULL" if c["nullable"] else " NOT NULL"))
        return "\n".join(lines)

    async def explain_query(self, sql: str) -> str:
        explain_format = os.getenv("EXPLAIN_FORMAT", "JSON")
        explain_analyze = os.getenv("EXPLAIN_ANALYZE", "false").lower() == "true"
        r = await self.execute_sql(f"EXPLAIN (FORMAT {explain_format}, ANALYZE {str(explain_analyze).lower()}) {sql}")
        if r.error:
            return f"Error: {r.error}"
        import json

        try:
            return json.dumps(r.rows[0][0] if r.rows else r.rows, indent=2, default=str)
        except Exception:
            return str(r.rows)

    async def get_health_report(self) -> str:
        checks = []
        r = await self.execute_sql("SELECT count(*)::int FROM pg_stat_activity WHERE state IS NOT NULL")
        if not r.error and r.rows:
            checks.append(f"Active connections: {r.rows[0][0]}")
        r = await self.execute_sql("SELECT count(*)::int FROM pg_stat_user_tables")
        if not r.error and r.rows:
            checks.append(f"User tables: {r.rows[0][0]}")
        r = await self.execute_sql("SELECT round(sum(pg_table_size(relid))::numeric/1024/1024,1)::text FROM pg_stat_user_tables")
        if not r.error and r.rows:
            checks.append(f"Total table size: {r.rows[0][0]} MB")
        r = await self.execute_sql("SELECT count(*)::int FROM pg_stat_user_indexes")
        if not r.error and r.rows:
            checks.append(f"Indexes: {r.rows[0][0]}")
        return "\n".join(checks) if checks else "No health data available"

    async def get_top_queries(self, limit: int = None) -> str:
        if limit is None:
            limit = int(os.getenv("TOP_QUERIES_DEFAULT_LIMIT", "10"))
        exclude_pattern = os.getenv("TOP_QUERIES_EXCLUDE_PATTERN", "%pg_stat%")
        r = await self.execute_sql(
            "SELECT query, calls, round(total_exec_time::numeric,1) AS total_ms, "
            "round(mean_exec_time::numeric,1) AS avg_ms, "
            "round(min_exec_time::numeric,1) AS min_ms, "
            "round(max_exec_time::numeric,1) AS max_ms, "
            "round((100*total_exec_time/sum(total_exec_time) OVER ())::numeric,1) AS pct "
            f"FROM pg_stat_statements WHERE query NOT LIKE '{exclude_pattern}' "
            f"ORDER BY total_exec_time DESC LIMIT {int(limit)}"
        )
        if r.error:
            return f"Requires pg_stat_statements: {r.error}"
        lines = ["Top Queries by Total Time:"]
        for i, row in enumerate(r.rows, 1):
            lines.append(f"{i}. ({row[6]}%) {row[0][:80]} \u2014 {row[2]}ms total, {row[3]}ms avg")
            lines.append(f"   Calls: {row[1]} | Min: {row[4]}ms | Max: {row[5]}ms")
        return "\n".join(lines)
