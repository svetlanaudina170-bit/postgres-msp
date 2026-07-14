# Анализ файла server.py
#
# Описание файла:
# Файл server.py реализует сервер для управления PostgreSQL базами данных через FastAPI.
# Он предоставляет инструменты для анализа схем, объектов, выполнения SQL запросов, анализа производительности
# и мониторинга здоровья базы данных. Поддерживает два режима доступа: unrestricted (без ограничений) и restricted
# (только чтение). Подключение к базе данных выполняется через пул соединений при вызове инструментов, используя
# DATABASE_URL и ACCESS_MODE, переданные от хоста. Сервер поддерживает JSON-RPC запросы через FastAPI,
# обработку сигналов для корректного завершения и настраиваемое логирование с индивидуализацией логов по пользователям.
#
# Используемые модули:
# - argparse: для обработки аргументов командной строки
# - asyncio: для асинхронного программирования
# - logging: для логирования событий
# - os, sys, signal: для работы с ОС и обработки сигналов
# - enum: для определения перечислений
# - typing: для аннотаций типов
# - fastapi: для создания HTTP-сервера
# - uvicorn: для запуска сервера
# - pydantic: для валидации входных данных
# - postgres_mcp: для специфичных инструментов PostgreSQL

import argparse
import asyncio
import logging
import os
import signal
import sys
import json
from enum import Enum
from typing import Any, List, Union, Dict, Literal
from dataclasses import dataclass, asdict
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from pydantic import Field
from postgres_mcp.database_health import DatabaseHealthTool, HealthType
from postgres_mcp.explain import ExplainPlanTool
from postgres_mcp.index.index_opt_base import MAX_NUM_INDEX_TUNING_QUERIES
from postgres_mcp.index.dta_calc import DatabaseTuningAdvisor
from postgres_mcp.index.llm_opt import LLMOptimizerTool
from postgres_mcp.index.presentation import TextPresentation
from postgres_mcp.top_queries.top_queries_calc import TopQueriesCalc
from postgres_mcp.sql import DbConnPool, SafeSqlDriver, SqlDriver, check_hypopg_installation_status, obfuscate_password

# Установка событийного цикла для Windows
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Инициализация логгера
logger = logging.getLogger(__name__)

# Изначально только консольное логирование
logging.basicConfig(level=logging.INFO)

# Переменная для хранения обработчика файла логов
file_handler = None

# Константы
PG_STAT_STATEMENTS: str = "pg_stat_statements"
HYPOPG_EXTENSION: str = "hypopg"

# Тип ответа
ResponseType = List[Dict[str, Any]]


# Режимы доступа
class AccessMode(str, Enum):
    UNRESTRICTED = "unrestricted"
    RESTRICTED = "restricted"


# Глобальные переменные
db_connection: DbConnPool = DbConnPool()
shutdown_in_progress: bool = False

# FastAPI приложение
app = FastAPI(title="Postgres MCP Tools Server")

# Добавляем CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def configure_logging(logging_config: Dict[str, Any]):
    """Настройка логирования на основе параметров от клиента"""
    global file_handler
    log_to_file = logging_config.get("logToFile", False)
    log_level = logging_config.get("logLevel", "INFO").upper()
    log_file = logging_config.get("logFile", "server.log")
    logs_path = logging_config.get("logsPath", "./logs")

    # Установить уровень логирования
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    logging.getLogger().setLevel(getattr(logging, log_level, logging.INFO))

    # Удалить предыдущий FileHandler, если он существует
    if file_handler:
        logger.removeHandler(file_handler)
        file_handler.close()

    if log_to_file:
        try:
            # Создаем директорию для логов
            os.makedirs(logs_path, exist_ok=True)
            log_file_path = os.path.join(logs_path, log_file)
            file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
            file_handler.setLevel(getattr(logging, log_level, logging.INFO))
            formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
            logger.info(f"File logging enabled to {log_file_path} with level {log_level}")
        except Exception as e:
            logger.error(f"Failed to configure file logging: {e}")


@dataclass
class MCPRequest:
    jsonrpc: str
    id: Any
    method: str
    params: Dict[str, Any] = None


@dataclass
class MCPResponse:
    jsonrpc: str = "2.0"
    id: Any = None
    result: Dict[str, Any] = None
    error: Dict[str, Any] = None

    def to_dict(self):
        response = {"jsonrpc": self.jsonrpc, "id": self.id}
        if self.result is not None:
            response["result"] = self.result
        if self.error is not None:
            response["error"] = self.error
        return response


@dataclass
class MCPTool:
    name: str
    description: str
    inputSchema: Dict[str, Any]


# Список инструментов
AVAILABLE_TOOLS = [
    MCPTool(
        name="list_schemas",
        description="Список всех схем в базе данных",
        inputSchema={
            "type": "object",
            "properties": {
                "database_url": {"type": "string", "description": "URL для подключения к базе (например, postgresql://user:pass@localhost:5432/db)"},
                "access_mode": {
                    "type": "string",
                    "enum": [AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
                    "default": AccessMode.UNRESTRICTED.value,
                    "description": "Режим доступа: 'unrestricted' или 'restricted'",
                },
            },
            "required": ["database_url"],
        },
    ),
    MCPTool(
        name="list_objects",
        description="Список объектов в указанной схеме",
        inputSchema={
            "type": "object",
            "properties": {
                "database_url": {"type": "string", "description": "URL для подключения к базе"},
                "schema_name": {"type": "string", "description": "Имя схемы"},
                "object_type": {
                    "type": "string",
                    "enum": ["table", "view", "sequence", "extension"],
                    "default": "table",
                    "description": "Тип объекта: 'table', 'view', 'sequence' или 'extension'",
                },
                "access_mode": {
                    "type": "string",
                    "enum": [AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
                    "default": AccessMode.UNRESTRICTED.value,
                    "description": "Режим доступа",
                },
            },
            "required": ["database_url", "schema_name"],
        },
    ),
    MCPTool(
        name="get_object_details",
        description="Подробная информация об объекте базы данных",
        inputSchema={
            "type": "object",
            "properties": {
                "database_url": {"type": "string", "description": "URL для подключения к базе"},
                "schema_name": {"type": "string", "description": "Имя схемы"},
                "object_name": {"type": "string", "description": "Имя объекта"},
                "object_type": {
                    "type": "string",
                    "enum": ["table", "view", "sequence", "extension"],
                    "default": "table",
                    "description": "Тип объекта",
                },
                "access_mode": {
                    "type": "string",
                    "enum": [AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
                    "default": AccessMode.UNRESTRICTED.value,
                    "description": "Режим доступа",
                },
            },
            "required": ["database_url", "schema_name", "object_name"],
        },
    ),
    MCPTool(
        name="explain_query",
        description="Объясняет план выполнения SQL запроса",
        inputSchema={
            "type": "object",
            "properties": {
                "database_url": {"type": "string", "description": "URL для подключения к базе"},
                "sql": {"type": "string", "description": "SQL запрос для анализа"},
                "analyze": {"type": "boolean", "description": "Выполнить запрос для реальной статистики", "default": False},
                "hypothetical_indexes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "table": {"type": "string", "description": "Имя таблицы"},
                            "columns": {"type": "array", "items": {"type": "string"}, "description": "Список столбцов"},
                            "using": {"type": "string", "description": "Метод индекса", "default": "btree"},
                        },
                        "required": ["table", "columns"],
                    },
                    "description": "Список гипотетических индексов",
                    "default": [],
                },
                "access_mode": {
                    "type": "string",
                    "enum": [AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
                    "default": AccessMode.UNRESTRICTED.value,
                    "description": "Режим доступа",
                },
            },
            "required": ["database_url", "sql"],
        },
    ),
    MCPTool(
        name="execute_sql",
        description="Выполнить SQL запрос",
        inputSchema={
            "type": "object",
            "properties": {
                "database_url": {"type": "string", "description": "URL для подключения к базе"},
                "sql": {"type": "string", "description": "SQL запрос для выполнения"},
                "access_mode": {
                    "type": "string",
                    "enum": [AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
                    "default": AccessMode.UNRESTRICTED.value,
                    "description": "Режим доступа",
                },
            },
            "required": ["database_url", "sql"],
        },
    ),
    MCPTool(
        name="analyze_workload_indexes",
        description="Анализ часто выполняемых запросов и рекомендация индексов",
        inputSchema={
            "type": "object",
            "properties": {
                "database_url": {"type": "string", "description": "URL для подключения к базе"},
                "max_index_size_mb": {"type": "integer", "description": "Максимальный размер индекса в МБ", "default": 10000},
                "method": {"type": "string", "enum": ["dta", "llm"], "default": "dta", "description": "Метод анализа: 'dta' или 'llm'"},
                "access_mode": {
                    "type": "string",
                    "enum": [AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
                    "default": AccessMode.UNRESTRICTED.value,
                    "description": "Режим доступа",
                },
            },
            "required": ["database_url"],
        },
    ),
    MCPTool(
        name="analyze_query_indexes",
        description="Анализ списка SQL запросов и рекомендация индексов",
        inputSchema={
            "type": "object",
            "properties": {
                "database_url": {"type": "string", "description": "URL для подключения к базе"},
                "queries": {"type": "array", "items": {"type": "string"}, "description": "Список SQL запросов"},
                "max_index_size_mb": {"type": "integer", "description": "Максимальный размер индекса в МБ", "default": 10000},
                "method": {"type": "string", "enum": ["dta", "llm"], "default": "dta", "description": "Метод анализа"},
                "access_mode": {
                    "type": "string",
                    "enum": [AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
                    "default": AccessMode.UNRESTRICTED.value,
                    "description": "Режим доступа",
                },
            },
            "required": ["database_url", "queries"],
        },
    ),
    MCPTool(
        name="analyze_db_health",
        description="Анализ здоровья базы данных",
        inputSchema={
            "type": "object",
            "properties": {
                "database_url": {"type": "string", "description": "URL для подключения к базе"},
                "health_type": {
                    "type": "string",
                    "description": f"Типы проверок: {', '.join(sorted([t.value for t in HealthType]))}",
                    "default": "all",
                },
                "access_mode": {
                    "type": "string",
                    "enum": [AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
                    "default": AccessMode.UNRESTRICTED.value,
                    "description": "Режим доступа",
                },
            },
            "required": ["database_url"],
        },
    ),
    MCPTool(
        name="get_top_queries",
        description=f"Список медленных или ресурсоемких запросов из {PG_STAT_STATEMENTS}",
        inputSchema={
            "type": "object",
            "properties": {
                "database_url": {"type": "string", "description": "URL для подключения к базе"},
                "sort_by": {
                    "type": "string",
                    "enum": ["total_time", "mean_time", "resources"],
                    "default": "resources",
                    "description": "Критерий сортировки",
                },
                "limit": {"type": "integer", "description": "Количество возвращаемых запросов", "default": 10},
                "access_mode": {
                    "type": "string",
                    "enum": [AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
                    "default": AccessMode.UNRESTRICTED.value,
                    "description": "Режим доступа",
                },
            },
            "required": ["database_url"],
        },
    ),
]


async def get_sql_driver(database_url: str, access_mode: str) -> Union[SqlDriver, SafeSqlDriver]:
    """Создает SQL драйвер для подключения."""
    logger.debug(f"Создание SQL драйвера для {obfuscate_password(database_url)} с режимом {access_mode}")
    try:
        await db_connection.pool_connect(database_url)
        base_driver = SqlDriver(conn=db_connection)
        if access_mode == AccessMode.RESTRICTED.value:
            logger.debug("Используется SafeSqlDriver (RESTRICTED mode)")
            return SafeSqlDriver(sql_driver=base_driver, timeout=30)
        logger.debug("Используется SqlDriver (UNRESTRICTED mode)")
        return base_driver
    except Exception as e:
        logger.error(f"Ошибка подключения к базе данных: {obfuscate_password(str(e))}")
        raise


def format_text_response(text: Any) -> ResponseType:
    """Форматирует текстовый ответ."""
    return [{"type": "text", "text": str(text)}]


def format_error_response(error: str) -> ResponseType:
    """Форматирует сообщение об ошибке."""
    return format_text_response(f"Ошибка: {obfuscate_password(error)}")


async def list_schemas(
    database_url: str = Field(description="URL для подключения к базе"),
    access_mode: str = Field(description="Режим доступа", default=AccessMode.UNRESTRICTED.value),
) -> ResponseType:
    logger.info(f"Вызов list_schemas с database_url={obfuscate_password(database_url)}, access_mode={access_mode}")
    try:
        sql_driver = await get_sql_driver(database_url, access_mode)
        rows = await sql_driver.execute_query(
            """
            SELECT
                schema_name,
                schema_owner,
                CASE
                    WHEN schema_name LIKE 'pg_%' THEN 'Системная схема'
                    WHEN schema_name = 'information_schema' THEN 'Системная информационная схема'
                    ELSE 'Пользовательская схема'
                END as schema_type
            FROM information_schema.schemata
            ORDER BY schema_type, schema_name
            """
        )
        schemas = [row.cells for row in rows] if rows else []
        await sql_driver.close()
        return format_text_response(schemas)
    except Exception as e:
        logger.error(f"Ошибка при получении схем: {obfuscate_password(str(e))}")
        return format_error_response(str(e))


async def list_objects(
    database_url: str = Field(description="URL для подключения к базе"),
    schema_name: str = Field(description="Имя схемы"),
    object_type: str = Field(description="Тип объекта", default="table"),
    access_mode: str = Field(description="Режим доступа", default=AccessMode.UNRESTRICTED.value),
) -> ResponseType:
    logger.info(
        f"Вызов list_objects с database_url={obfuscate_password(database_url)}, schema_name={schema_name}, object_type={object_type}, access_mode={access_mode}"
    )
    try:
        sql_driver = await get_sql_driver(database_url, access_mode)
        if object_type in ("table", "view"):
            table_type = "BASE TABLE" if object_type == "table" else "VIEW"
            rows = await SafeSqlDriver.execute_param_query(
                sql_driver,
                """
                SELECT table_schema, table_name, table_type
                FROM information_schema.tables
                WHERE table_schema = {} AND table_type = {}
                ORDER BY table_name
                """,
                [schema_name, table_type],
            )
            objects = (
                [{"schema": row.cells["table_schema"], "name": row.cells["table_name"], "type": row.cells["table_type"]} for row in rows]
                if rows
                else []
            )
        elif object_type == "sequence":
            rows = await SafeSqlDriver.execute_param_query(
                sql_driver,
                """
                SELECT sequence_schema, sequence_name, data_type
                FROM information_schema.sequences
                WHERE sequence_schema = {}
                ORDER BY sequence_name
                """,
                [schema_name],
            )
            objects = (
                [{"schema": row.cells["sequence_schema"], "name": row.cells["sequence_name"], "data_type": row.cells["data_type"]} for row in rows]
                if rows
                else []
            )
        elif object_type == "extension":
            rows = await sql_driver.execute_query(
                """
                SELECT extname, extversion, extrelocatable
                FROM pg_extension
                ORDER BY extname
                """
            )
            objects = (
                [{"name": row.cells["extname"], "version": row.cells["extversion"], "relocatable": row.cells["extrelocatable"]} for row in rows]
                if rows
                else []
            )
        else:
            return format_error_response(f"Неподдерживаемый тип объекта: {object_type}")
        await sql_driver.close()
        return format_text_response(objects)
    except Exception as e:
        logger.error(f"Ошибка при получении объектов: {obfuscate_password(str(e))}")
        return format_error_response(str(e))


async def get_object_details(
    database_url: str = Field(description="URL для подключения к базе"),
    schema_name: str = Field(description="Имя схемы"),
    object_name: str = Field(description="Имя объекта"),
    object_type: str = Field(description="Тип объекта", default="table"),
    access_mode: str = Field(description="Режим доступа", default=AccessMode.UNRESTRICTED.value),
) -> ResponseType:
    logger.info(
        f"Вызов get_object_details с database_url={obfuscate_password(database_url)}, schema_name={schema_name}, object_name={object_name}, object_type={object_type}, access_mode={access_mode}"
    )
    try:
        sql_driver = await get_sql_driver(database_url, access_mode)
        if object_type in ("table", "view"):
            col_rows = await SafeSqlDriver.execute_param_query(
                sql_driver,
                """
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_schema = {} AND table_name = {}
                ORDER BY ordinal_position
                """,
                [schema_name, object_name],
            )
            columns = (
                [
                    {
                        "column": r.cells["column_name"],
                        "data_type": r.cells["data_type"],
                        "is_nullable": r.cells["is_nullable"],
                        "default": r.cells["column_default"],
                    }
                    for r in col_rows
                ]
                if col_rows
                else []
            )
            con_rows = await SafeSqlDriver.execute_param_query(
                sql_driver,
                """
                SELECT tc.constraint_name, tc.constraint_type, kcu.column_name
                FROM information_schema.table_constraints AS tc
                LEFT JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
                WHERE tc.table_schema = {} AND tc.table_name = {}
                """,
                [schema_name, object_name],
            )
            constraints = (
                [
                    {
                        "constraint_name": r.cells["constraint_name"],
                        "constraint_type": r.cells["constraint_type"],
                        "column_name": r.cells["column_name"],
                    }
                    for r in con_rows
                ]
                if con_rows
                else []
            )
            details = {"columns": columns, "constraints": constraints}
            await sql_driver.close()
            return format_text_response(details)
        else:
            return format_error_response(f"Неподдерживаемый тип объекта: {object_type}")
    except Exception as e:
        logger.error(f"Ошибка при получении деталей объекта: {obfuscate_password(str(e))}")
        return format_error_response(str(e))


async def explain_query(
    database_url: str = Field(description="URL для подключения к базе"),
    sql: str = Field(description="SQL запрос для анализа"),
    analyze: bool = Field(description="Выполнить запрос для реальной статистики", default=False),
    hypothetical_indexes: List[Dict[str, Any]] = Field(description="Список гипотетических индексов", default=[]),
    access_mode: str = Field(description="Режим доступа", default=AccessMode.UNRESTRICTED.value),
) -> ResponseType:
    logger.info(
        f"Вызов explain_query с database_url={obfuscate_password(database_url)}, sql={sql[:50]}..., analyze={analyze}, access_mode={access_mode}"
    )
    try:
        sql_driver = await get_sql_driver(database_url, access_mode)
        tool = ExplainPlanTool(sql_driver=sql_driver)
        result = await tool.explain_plan(sql=sql, analyze=analyze, hypo_indexes=hypothetical_indexes)
        await sql_driver.close()
        return format_text_response(result.to_dict())
    except Exception as e:
        logger.error(f"Ошибка при анализе запроса: {obfuscate_password(str(e))}")
        return format_error_response(str(e))


async def execute_sql(
    database_url: str = Field(description="URL для подключения к базе"),
    sql: str = Field(description="SQL запрос для выполнения"),
    access_mode: str = Field(description="Режим доступа", default=AccessMode.UNRESTRICTED.value),
) -> ResponseType:
    logger.info(f"Вызов execute_sql с database_url={obfuscate_password(database_url)}, sql={sql[:50]}..., access_mode={access_mode}")
    try:
        sql_driver = await get_sql_driver(database_url, access_mode)
        rows = await sql_driver.execute_query(sql)
        result = [row.cells for row in rows] if rows else []
        await sql_driver.close()
        return format_text_response(result)
    except Exception as e:
        logger.error(f"Ошибка при выполнении SQL: {obfuscate_password(str(e))}")
        return format_error_response(str(e))


async def analyze_workload_indexes(
    database_url: str = Field(description="URL для подключения к базе"),
    max_index_size_mb: int = Field(description="Максимальный размер индекса в МБ", default=10000),
    method: Literal["dta", "llm"] = Field(description="Метод анализа", default="dta"),
    access_mode: str = Field(description="Режим доступа", default=AccessMode.UNRESTRICTED.value),
) -> ResponseType:
    logger.info(f"Вызов analyze_workload_indexes с database_url={obfuscate_password(database_url)}, method={method}, access_mode={access_mode}")
    try:
        sql_driver = await get_sql_driver(database_url, access_mode)
        if method == "dta":
            tool = DatabaseTuningAdvisor(sql_driver=sql_driver, max_index_size_mb=max_index_size_mb)
            recommendations = await tool.get_index_recommendations()
        else:
            tool = LLMOptimizerTool(sql_driver=sql_driver, max_index_size_mb=max_index_size_mb)
            recommendations = await tool.get_index_recommendations()
        await sql_driver.close()
        return format_text_response(TextPresentation.to_text(recommendations))
    except Exception as e:
        logger.error(f"Ошибка при анализе рабочей нагрузки: {obfuscate_password(str(e))}")
        return format_error_response(str(e))


async def analyze_query_indexes(
    database_url: str = Field(description="URL для подключения к базе"),
    queries: List[str] = Field(description="Список SQL запросов"),
    max_index_size_mb: int = Field(description="Максимальный размер индекса в МБ", default=10000),
    method: Literal["dta", "llm"] = Field(description="Метод анализа", default="dta"),
    access_mode: str = Field(description="Режим доступа", default=AccessMode.UNRESTRICTED.value),
) -> ResponseType:
    logger.info(
        f"Вызов analyze_query_indexes с database_url={obfuscate_password(database_url)}, queries_count={len(queries)}, method={method}, access_mode={access_mode}"
    )
    try:
        if len(queries) > MAX_NUM_INDEX_TUNING_QUERIES:
            return format_error_response(f"Слишком много запросов: {len(queries)}. Максимум: {MAX_NUM_INDEX_TUNING_QUERIES}")
        sql_driver = await get_sql_driver(database_url, access_mode)
        if method == "dta":
            tool = DatabaseTuningAdvisor(sql_driver=sql_driver, max_index_size_mb=max_index_size_mb)
            recommendations = await tool.get_index_recommendations(queries=queries)
        else:
            tool = LLMOptimizerTool(sql_driver=sql_driver, max_index_size_mb=max_index_size_mb)
            recommendations = await tool.get_index_recommendations(queries=queries)
        await sql_driver.close()
        return format_text_response(TextPresentation.to_text(recommendations))
    except Exception as e:
        logger.error(f"Ошибка при анализе запросов: {obfuscate_password(str(e))}")
        return format_error_response(str(e))


async def analyze_db_health(
    database_url: str = Field(description="URL для подключения к базе"),
    health_type: str = Field(description=f"Типы проверок: {', '.join(sorted([t.value for t in HealthType]))}", default="all"),
    access_mode: str = Field(description="Режим доступа", default=AccessMode.UNRESTRICTED.value),
) -> ResponseType:
    logger.info(f"Вызов analyze_db_health с database_url={obfuscate_password(database_url)}, health_type={health_type}, access_mode={access_mode}")
    try:
        sql_driver = await get_sql_driver(database_url, access_mode)
        tool = DatabaseHealthTool(sql_driver=sql_driver)
        health_report = await tool.analyze_health(health_type=health_type)
        await sql_driver.close()
        return format_text_response(health_report.to_dict())
    except Exception as e:
        logger.error(f"Ошибка при анализе здоровья базы: {obfuscate_password(str(e))}")
        return format_error_response(str(e))


async def get_top_queries(
    database_url: str = Field(description="URL для подключения к базе"),
    sort_by: Literal["total_time", "mean_time", "resources"] = Field(description="Критерий сортировки", default="resources"),
    limit: int = Field(description="Количество возвращаемых запросов", default=10),
    access_mode: str = Field(description="Режим доступа", default=AccessMode.UNRESTRICTED.value),
) -> ResponseType:
    logger.info(
        f"Вызов get_top_queries с database_url={obfuscate_password(database_url)}, sort_by={sort_by}, limit={limit}, access_mode={access_mode}"
    )
    try:
        sql_driver = await get_sql_driver(database_url, access_mode)
        tool = TopQueriesCalc(sql_driver=sql_driver)
        queries = await tool.get_top_queries(sort_by=sort_by, limit=limit)
        await sql_driver.close()
        return format_text_response([q.to_dict() for q in queries])
    except Exception as e:
        logger.error(f"Ошибка при получении топ-запросов: {obfuscate_password(str(e))}")
        return format_error_response(str(e))


@app.post("/")
async def handle_jsonrpc_request(request: Request):
    """Обработка JSON-RPC запросов"""
    try:
        json_data = await request.json()
        mcp_request = MCPRequest(**json_data)

        if mcp_request.method == "initialize":
            configure_logging(mcp_request.params.get("capabilities", {}).get("loggingConfig", {}))
            logger.info("Инициализация сервера")
            return MCPResponse(
                id=mcp_request.id,
                result={
                    "serverInfo": {"name": "Postgres MCP Tools Server", "version": "1.0.0"},
                    "capabilities": {"tools": {tool.name: asdict(tool) for tool in AVAILABLE_TOOLS}},
                },
            ).to_dict()

        elif mcp_request.method == "tools/list":
            logger.info("Запрос списка инструментов")
            return MCPResponse(id=mcp_request.id, result={"tools": [asdict(tool) for tool in AVAILABLE_TOOLS]}).to_dict()

        elif mcp_request.method == "tools/call":
            tool_name = mcp_request.params.get("name")
            arguments = mcp_request.params.get("arguments", {})
            logger.info(f"Вызов инструмента: {tool_name} с аргументами: {obfuscate_password(str(arguments))}")

            tool_functions = {
                "list_schemas": list_schemas,
                "list_objects": list_objects,
                "get_object_details": get_object_details,
                "explain_query": explain_query,
                "execute_sql": execute_sql,
                "analyze_workload_indexes": analyze_workload_indexes,
                "analyze_query_indexes": analyze_query_indexes,
                "analyze_db_health": analyze_db_health,
                "get_top_queries": get_top_queries,
            }

            if tool_name not in tool_functions:
                return MCPResponse(id=mcp_request.id, error={"code": -32601, "message": f"Инструмент {tool_name} не найден"}).to_dict()

            try:
                result = await tool_functions[tool_name](**arguments)
                return MCPResponse(id=mcp_request.id, result={"content": result}).to_dict()
            except Exception as e:
                logger.error(f"Ошибка выполнения инструмента {tool_name}: {obfuscate_password(str(e))}")
                return MCPResponse(id=mcp_request.id, error={"code": -32000, "message": obfuscate_password(str(e))}).to_dict()

        else:
            return MCPResponse(id=mcp_request.id, error={"code": -32601, "message": f"Метод {mcp_request.method} не найден"}).to_dict()

    except Exception as e:
        logger.error(f"Ошибка обработки JSON-RPC запроса: {obfuscate_password(str(e))}")
        return MCPResponse(id=None, error={"code": -32700, "message": obfuscate_password(str(e))}).to_dict()


def parse_args():
    """Разбор аргументов командной строки"""
    parser = argparse.ArgumentParser(description="Postgres MCP Tools Server")
    parser.add_argument("--sse-host", default="localhost", help="SSE host (default: localhost)")
    parser.add_argument("--sse-port", type=int, default=5001, help="SSE port (default: 5001)")
    parser.add_argument(
        "--access-mode",
        choices=[AccessMode.UNRESTRICTED.value, AccessMode.RESTRICTED.value],
        default=AccessMode.UNRESTRICTED.value,
        help="Default access mode",
    )
    return parser.parse_args()


async def shutdown():
    """Корректное завершение работы сервера"""
    global shutdown_in_progress
    if shutdown_in_progress:
        return
    shutdown_in_progress = True
    logger.info("Завершение работы сервера...")
    await db_connection.close()
    logger.info("Пул соединений закрыт")
    if file_handler:
        file_handler.close()
        logger.removeHandler(file_handler)
    logger.info("Сервер завершил работу")


def handle_shutdown(loop):
    """Обработка сигналов завершения"""
    tasks = [task for task in asyncio.all_tasks(loop) if task is not asyncio.current_task()]
    for task in tasks:
        task.cancel()
    loop.run_until_complete(loop.shutdown_asyncgens())
    loop.run_until_complete(shutdown())
    loop.close()


if __name__ == "__main__":
    args = parse_args()

    # Установка глобального режима доступа по умолчанию
    current_access_mode = AccessMode(args.access_mode)
    logger.info(f"Сервер запущен с режимом доступа по умолчанию: {current_access_mode.value}")

    # Запуск FastAPI сервера
    try:
        uvicorn.run(app, host=args.sse_host, port=args.sse_port)
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания")
    except Exception as e:
        logger.error(f"Ошибка запуска сервера: {obfuscate_password(str(e))}")
