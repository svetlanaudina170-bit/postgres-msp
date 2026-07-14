# Анализ файла safe_sql.py
#
# Описание файла:
# Файл safe_sql.py содержит класс SafeSqlDriver, который является оберткой над SqlDriver для безопасного выполнения
# SQL-запросов. Он ограничивает выполнение только безопасными операциями (SELECT, ANALYZE, VACUUM, EXPLAIN, SHOW и др.),
# используя библиотеку pglast для парсинга и валидации запросов. Класс предотвращает выполнение небезопасных операций
# (DDL, DML и т.д.) и проверяет запросы на наличие допустимых функций и узлов AST.
#
# Используемые модули:
# - asyncio: для асинхронного выполнения запросов
# - logging: для логирования событий
# - re: для работы с регулярными выражениями
# - typing: для аннотаций типов
# - pglast: для парсинга SQL и работы с AST
# - psycopg.sql: для безопасной обработки SQL-запросов и параметров
# - typing_extensions: для поддержки LiteralString
#
# Импорты:
# - SqlDriver: базовый класс для выполнения SQL-запросов
#
# Основные компоненты:
# - Класс SafeSqlDriver: обертка над SqlDriver с валидацией запросов
# - Константы ALLOWED_STMT_TYPES, ALLOWED_FUNCTIONS, ALLOWED_NODE_TYPES, ALLOWED_EXTENSIONS: списки разрешенных типов узлов,
#   функций и расширений
# - Методы для валидации и выполнения запросов
#
# Зависимости:
# Файл зависит от sql_driver и используется в других модулях пакета, таких как extension_utils.py и bind_params.py,
# для безопасного выполнения запросов к PostgreSQL.

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, ClassVar, List, Optional, Set, Type, Union

import pglast
from pglast.ast import (
    A_ArrayExpr,
    A_Const,
    A_Expr,
    A_Indices,
    A_Indirection,
    A_Star,
    Alias,
    BitString,
    Boolean,
    BooleanTest,
    BoolExpr,
    CaseExpr,
    CaseWhen,
    ClosePortalStmt,
    CoalesceExpr,
    CollateClause,
    ColumnRef,
    CommonTableExpr,
    CreateExtensionStmt,
    DeallocateStmt,
    DeclareCursorStmt,
    DefElem,
    ExplainStmt,
    FetchStmt,
    Float,
    FromExpr,
    FuncCall,
    GroupingFunc,
    GroupingSet,
    Integer,
    JoinExpr,
    MinMaxExpr,
    NamedArgExpr,
    Node,
    NotifyStmt,
    NullTest,
    ParamRef,
    PrepareStmt,
    RangeFunction,
    RangeSubselect,
    RangeTableFunc,
    RangeTableFuncCol,
    RangeTableSample,
    RangeVar,
    RawStmt,
    ResTarget,
    RowCompareExpr,
    RowExpr,
    ScalarArrayOpExpr,
    SelectStmt,
    SortBy,
    SortGroupClause,
    SQLValueFunction,
    String,
    SubLink,
    TableFunc,
    TableSampleClause,
    TargetEntry,
    TypeCast,
    TypeName,
    VacuumStmt,
    VariableShowStmt,
    WithClause,
    WindowClause,
    WindowDef,
    WindowFunc,
)
from pglast.enums import A_Expr_Kind
from psycopg.sql import SQL, Composable, Literal
from typing_extensions import LiteralString

from .sql_driver import SqlDriver

# Инициализация логгера
logger = logging.getLogger(__name__)


# Описание класса SafeSqlDriver
#
# Класс SafeSqlDriver оборачивает SqlDriver, ограничивая выполнение запросов только безопасными операциями.
# Использует pglast для парсинга и валидации SQL-запросов, предотвращая выполнение DDL, DML и других
# потенциально опасных операций.
class SafeSqlDriver(SqlDriver):
    """Обертка над SqlDriver, допускающая только SELECT, ANALYZE, VACUUM, EXPLAIN SELECT и SHOW запросы.

    Использует pglast для парсинга и валидации SQL-запросов перед выполнением.
    Все остальные типы запросов (DDL, DML и т.д.) отклоняются.
    Выполняет глубокую валидацию дерева запросов для предотвращения небезопасных операций.
    """

    # Регулярное выражение для проверки квалификации схемы pg_catalog
    PG_CATALOG_PATTERN: ClassVar[re.Pattern] = re.compile(r"^pg_catalog\.(.+)$")
    # Регулярное выражение для проверки LIKE-выражений (должны начинаться или заканчиваться %, но не оба)
    LIKE_PATTERN: ClassVar[re.Pattern] = re.compile(r"^[^%]+%$")

    # Разрешенные типы узлов AST для запросов
    ALLOWED_STMT_TYPES: ClassVar[Set[Type[Node]]] = {
        SelectStmt,  # Обычные SELECT-запросы
        ExplainStmt,  # EXPLAIN SELECT
        CreateExtensionStmt,  # CREATE EXTENSION
        VariableShowStmt,  # SHOW-запросы
        VacuumStmt,  # VACUUM и ANALYZE
        PrepareStmt,  # PREPARE (для подготовленных запросов)
        DeallocateStmt,  # DEALLOCATE (для подготовленных запросов)
        DeclareCursorStmt,  # DECLARE CURSOR
        ClosePortalStmt,  # CLOSE (для закрытия курсоров)
        FetchStmt,  # FETCH (для получения результатов курсора)
    }

    # Разрешенные функции PostgreSQL
    ALLOWED_FUNCTIONS: ClassVar[Set[str]] = {
        # Агрегатные функции
        "array_agg",
        "avg",
        "bit_and",
        "bit_or",
        "bool_and",
        "bool_or",
        "count",
        "every",
        "json_agg",
        "jsonb_agg",
        "json_object_agg",
        "jsonb_object_agg",
        "max",
        "min",
        "string_agg",
        "sum",
        "xmlagg",
        # Математические функции
        "abs",
        "cbrt",
        "ceil",
        "ceiling",
        "degrees",
        "div",
        "erf",
        "erfc",
        "exp",
        "factorial",
        "floor",
        "gcd",
        "lcm",
        "ln",
        "log",
        "log10",
        "min_scale",
        "mod",
        "pi",
        "power",
        "radians",
        "random",
        "random_normal",
        "round",
        "scale",
        "setseed",
        "sign",
        "sqrt",
        "trim_scale",
        "trunc",
        "width_bucket",
        # Тригонометрические функции
        "acos",
        "acosd",
        "asin",
        "asind",
        "atan",
        "atand",
        "atan2",
        "atan2d",
        "cos",
        "cosd",
        "cot",
        "cotd",
        "sin",
        "sind",
        "tan",
        "tand",
        # Гиперболические функции
        "sinh",
        "cosh",
        "tanh",
        "asinh",
        "acosh",
        "atanh",
        # Функции для работы с массивами
        "array",
        "array_append",
        "array_cat",
        "array_dims",
        "array_fill",
        "array_length",
        "array_lower",
        "array_ndims",
        "array_position",
        "array_positions",
        "array_prepend",
        "array_remove",
        "array_replace",
        "array_sample",
        "array_shuffle",
        "array_to_string",
        "array_upper",
        "cardinality",
        "string_to_array",
        "trim_array",
        "unnest",
        "any",
        # Строковые функции
        "ascii",
        "bit_length",
        "btrim",
        "char_length",
        "character_length",
        "chr",
        "concat",
        "concat_ws",
        "convert",
        "convert_from",
        "convert_to",
        "decode",
        "encode",
        "format",
        "initcap",
        "left",
        "length",
        "lower",
        "lpad",
        "ltrim",
        "md5",
        "normalize",
        "octet_length",
        "overlay",
        "parse_ident",
        "position",
        "quote_ident",
        "quote_literal",
        "quote_nullable",
        "repeat",
        "replace",
        "reverse",
        "right",
        "rpad",
        "rtrim",
        "split_part",
        "starts_with",
        "string_to_table",
        "strpos",
        "substr",
        "substring",
        "to_ascii",
        "to_bin",
        "to_hex",
        "to_oct",
        "translate",
        "trim",
        "upper",
        "unistr",
        "unicode_assigned",
        # Функции для работы с регулярными выражениями
        "regexp_match",
        "regexp_matches",
        "regexp_replace",
        "regexp_split_to_array",
        "regexp_split_to_table",
        "regexp_substr",
        "regexp_count",
        "regexp_instr",
        "regexp_like",
        # Функции приведения типов
        "regclass",
        "to_char",
        "to_date",
        "to_number",
        "to_timestamp",
        # Информационные функции
        "current_catalog",
        "current_database",
        "current_query",
        "current_role",
        "current_schema",
        "current_schemas",
        "current_setting",
        "current_user",
        "pg_backend_pid",
        "pg_blocking_pids",
        "pg_conf_load_time",
        "pg_current_logfile",
        "pg_jit_available",
        "pg_safe_snapshot_blocking_pids",
        "pg_trigger_depth",
        "session_user",
        "system_user",
        "user",
        "version",
        "unicode_version",
        "icu_unicode_version",
        # Функции информации об объектах базы данных
        "pg_column_size",
        "pg_column_compression",
        "pg_column_toast_chunk_id",
        "pg_database_size",
        "pg_indexes_size",
        "pg_get_indexdef",
        "pg_relation_filenode",
        "pg_relation_size",
        "pg_size_bytes",
        "pg_size_pretty",
        "pg_table_size",
        "pg_tablespace_size",
        "pg_total_relation_size",
        # Функции проверки привилегий
        "has_any_column_privilege",
        "has_column_privilege",
        "has_database_privilege",
        "has_foreign_data_wrapper_privilege",
        "has_function_privilege",
        "has_language_privilege",
        "has_parameter_privilege",
        "has_schema_privilege",
        "has_sequence_privilege",
        "has_server_privilege",
        "has_table_privilege",
        "has_tablespace_privilege",
        "has_type_privilege",
        "pg_has_role",
        "row_security_active",
        # JSON-функции
        "json",
        "json_array_length",
        "jsonb_array_length",
        "json_each",
        "jsonb_each",
        "json_each_text",
        "jsonb_each_text",
        "json_extract_path",
        "jsonb_extract_path",
        "json_extract_path_text",
        "jsonb_extract_path_text",
        "json_object_keys",
        "jsonb_object_keys",
        "json_array_elements",
        "jsonb_array_elements",
        "json_array_elements_text",
        "jsonb_array_elements_text",
        "json_typeof",
        "jsonb_typeof",
        "json_strip_nulls",
        "jsonb_strip_nulls",
        "jsonb_set",
        "jsonb_set_path",
        "jsonb_set_lax",
        "jsonb_pretty",
        "json_build_array",
        "jsonb_build_array",
        "json_build_object",
        "jsonb_build_object",
        "json_object",
        "jsonb_object",
        "json_scalar",
        "json_serialize",
        "json_populate_record",
        "jsonb_populate_record",
        "jsonb_populate_record_valid",
        "json_populate_recordset",
        "jsonb_populate_recordset",
        "json_to_record",
        "jsonb_to_record",
        "json_to_recordset",
        "jsonb_to_recordset",
        "jsonb_insert",
        "jsonb_path_exists",
        "jsonb_path_match",
        "jsonb_path_query",
        "jsonb_path_query_array",
        "jsonb_path_query_first",
        "jsonb_path_exists_tz",
        "jsonb_path_match_tz",
        "jsonb_path_query_tz",
        "jsonb_path_query_array_tz",
        "jsonb_path_query_first_tz",
        "jsonbv_typeof",
        "to_json",
        "to_jsonb",
        "array_to_json",
        "row_to_json",
        # Функции информации об объектах
        "pg_get_expr",
        "pg_get_functiondef",
        "pg_get_function_arguments",
        "pg_get_function_identity_arguments",
        "pg_get_function_result",
        "pg_get_catalog_foreign_keys",
        "pg_get_constraintdef",
        "pg_get_userbyid",
        "pg_get_keywords",
        "pg_get_partkeydef",
        # Функции кодирования
        "pg_basetype",
        "pg_client_encoding",
        "pg_encoding_to_char",
        "pg_char_to_encoding",
        # Функции проверки валидности
        "pg_input_is_valid",
        "pg_input_error_info",
        # Функции информации об объектах
        "pg_get_serial_sequence",
        "pg_get_viewdef",
        "pg_get_ruledef",
        "pg_get_triggerdef",
        "pg_get_statisticsobjdef",
        # Функции информации о типах
        "pg_typeof",
        "format_type",
        "to_regtype",
        "to_regtypemod",
        # Функции преобразования имен объектов
        "to_regclass",
        "to_regcollation",
        "to_regnamespace",
        "to_regoper",
        "to_regoperator",
        "to_regproc",
        "to_regprocedure",
        "to_regrole",
        # Функции свойств индексов
        "pg_index_column_has_property",
        "pg_index_has_property",
        "pg_indexam_has_property",
        # Функции видимости схемы
        "pg_collation_is_visible",
        "pg_conversion_is_visible",
        "pg_function_is_visible",
        "pg_opclass_is_visible",
        "pg_operator_is_visible",
        "pg_opfamily_is_visible",
        "pg_statistics_obj_is_visible",
        "pg_table_is_visible",
        "pg_ts_config_is_visible",
        "pg_ts_dict_is_visible",
        "pg_ts_parser_is_visible",
        "pg_ts_template_is_visible",
        "pg_type_is_visible",
        # Функции даты и времени
        "age",
        "clock_timestamp",
        "current_date",
        "current_time",
        "current_timestamp",
        "date_part",
        "date_trunc",
        "extract",
        "isfinite",
        "justify_days",
        "justify_hours",
        "justify_interval",
        "localtime",
        "localtimestamp",
        "make_date",
        "make_interval",
        "make_time",
        "make_timestamp",
        "make_timestamptz",
        "now",
        "statement_timestamp",
        "timeofday",
        "transaction_timestamp",
        # Функции приведения типов
        "cast",
        "text",
        "bool",
        "int2",
        "int4",
        "int8",
        "float4",
        "float8",
        "numeric",
        "date",
        "time",
        "timetz",
        "timestamp",
        "timestamptz",
        "interval",
        # ACL-функции
        "acldefault",
        "aclexplode",
        "makeaclitem",
        # Функции системной информации
        "pg_tablespace_location",
        "pg_tablespace_databases",
        "pg_settings_get_flags",
        "pg_options_to_table",
        # Функции транзакций/WAL
        "pg_current_xact_id",
        "pg_current_snapshot",
        "pg_snapshot_xip",
        "pg_xact_commit_timestamp",
        # Функции управления WAL
        "pg_control_checkpoint",
        "pg_control_system",
        "pg_control_init",
        "pg_control_recovery",
        # Функции WAL
        "pg_available_wal_summaries",
        "pg_wal_summary_contents",
        "pg_get_wal_summarizer_state",
        # Функции сети
        "inet_client_addr",
        "inet_client_port",
        "inet_server_addr",
        "inet_server_port",
        # Функции временных схем
        "pg_my_temp_schema",
        "pg_is_other_temp_schema",
        # Функции уведомлений
        "pg_listening_channels",
        "pg_notification_queue_usage",
        # Функции состояния сервера
        "pg_postmaster_start_time",
        # Функции восстановления
        "pg_is_in_recovery",
        # Функции HypoPG
        "hypopg_create_index",
        "hypopg_reset",
        "hypopg_relation_size",
        "hypopg_list_indexes",
        "hypopg_get_indexdef",
        "hypopg_hide_index",
        "hypopg_unhide_index",
        # XML-функции
        "xml",
        "xmlcomment",
        "xmlconcat",
        "xmlelement",
        "xmlforest",
        "xmlpi",
        "xmlroot",
        "xmlexists",
        "xml_is_well_formed",
        "xml_is_well_formed_document",
        "xml_is_well_formed_content",
        "xpath",
        "xpath_exists",
        "xmltable",
        "xmlnamespaces",
        # Функции работы с сетью/IP
        "abbrev",
        "broadcast",
        "cidr_split",
        "family",
        "host",
        "hostmask",
        "inet_merge",
        "inet_same_family",
        "macaddr8_set7bit",
        "masklen",
        "netmask",
        "network",
        "set_bit",
        "set_masklen",
        # Функции полнотекстового поиска
        "array_to_tsvector",
        "get_current_ts_config",
        "numnode",
        "plainto_tsquery",
        "phraseto_tsquery",
        "querytree",
        "setweight",
        "strip",
        "to_tsquery",
        "to_tsvector",
        "ts_debug",
        "ts_delete",
        "ts_filter",
        "ts_headline",
        "ts_lexize",
        "ts_parse",
        "ts_rank",
        "ts_rank_cd",
        "ts_rewrite",
        "ts_stat",
        "ts_token_type",
        "tsvector_to_array",
        "websearch_to_tsquery",
        # Функции диапазонов
        "isempty",
        "lower_inc",
        "upper_inc",
        "lower_inf",
        "upper_inf",
        "range_merge",
        # Геометрические функции
        "area",
        "center",
        "diameter",
        "height",
        "isclosed",
        "isopen",
        "npoints",
        "pclose",
        "popen",
        "radius",
        "width",
        # UUID-функции
        "gen_random_uuid",
        # Функции перечислений
        "enum_first",
        "enum_last",
        "enum_range",
        # Функции последовательностей
        "currval",
        "lastval",
        # Функции pg_trgm
        "similarity",
        "show_trgm",
        "show_limit",
        "word_similarity",
        "strict_word_similarity",
        # Функции pg_crypto
        "digest",
        "hmac",
        "encrypt",
        "gen_salt",
        "crypt",
        # Функции earthdistance/cube
        "earth_distance",
        "earth_box",
        "ll_to_earth",
        "earth_to_ll",
        "cube_distance",
        # Функции fuzzystrmatch
        "soundex",
        "difference",
        "levenshtein",
        "metaphone",
        "dmetaphone",
        # Оконные функции
        "row_number",
        "rank",
        "dense_rank",
        "percent_rank",
        "cume_dist",
        "ntile",
        "lag",
        "lead",
        "first_value",
        "last_value",
        "nth_value",
    }

    # Разрешенные типы узлов AST
    ALLOWED_NODE_TYPES: ClassVar[Set[Type[Node]]] = ALLOWED_STMT_TYPES | {
        ResTarget,
        ColumnRef,
        A_Star,
        A_Const,
        A_Expr,
        BoolExpr,
        BooleanTest,
        NullTest,
        RangeVar,
        JoinExpr,
        FromExpr,
        WithClause,
        CommonTableExpr,
        SubLink,
        MinMaxExpr,
        RowExpr,
        DefElem,
        SortBy,
        SortGroupClause,
        Integer,
        Float,
        String,
        BitString,
        Boolean,
        RawStmt,
        ParamRef,
        SQLValueFunction,
        FuncCall,
        TypeCast,
        TypeName,
        Alias,
        CaseExpr,
        CaseWhen,
        RangeSubselect,
        CoalesceExpr,
        NamedArgExpr,
        RangeFunction,
        A_ArrayExpr,
        WindowFunc,
        WindowDef,
        WindowClause,
        TableFunc,
        RangeTableFunc,
        RangeTableFuncCol,
        A_Indirection,
        A_Indices,
        GroupingSet,
        GroupingFunc,
        RangeTableSample,
        TableSampleClause,
        RowCompareExpr,
        CollateClause,
        TargetEntry,
        ScalarArrayOpExpr,
        NotifyStmt,
    }

    # Разрешенные расширения PostgreSQL
    ALLOWED_EXTENSIONS: ClassVar[Set[str]] = {
        "hypopg",
        "pg_stat_statements",
        "pg_trgm",
        "btree_gin",
        "btree_gist",
        "earthdistance",
        "cube",
        "fuzzystrmatch",
        "intarray",
        "pgcrypto",
        "hstore",
        "ltree",
        "xml2",
        "tablefunc",
        "tsm_system_rows",
        "tsm_system_time",
        "unaccent",
        "uuid-ossp",
        "adminpack",
        "amcheck",
        "bloom",
        "citext",
        "dict_int",
        "dict_xsyn",
        "file_fdw",
        "intagg",
        "isn",
        "lo",
        "pg_buffercache",
        "pg_freespacemap",
        "pg_prewarm",
        "pg_visibility",
        "pgrowlocks",
        "pgstattuple",
        "plpgsql",
        "seg",
        "spi",
        "sslinfo",
        "postgres_fdw",
        "dblink",
        "mysql_fdw",
        "mongo_fdw",
        "tds_fdw",
        "oracle_fdw",
        "postgis",
        "pgrouting",
        "timescaledb",
        "pg_partman",
        "orafce",
        "pgaudit",
        "pgtap",
        "pgsphere",
        "pg_qualstats",
        "pg_hint_plan",
        "auto_explain",
        "pg_wait_sampling",
        "plv8",
        "pg_stat_monitor",
        "pg_cron",
        "pglogical",
        "pgq",
        "pgpool_adm",
        "pg_fuzzystrmatch",
        "pg_bigm",
        "pgvector",
        "rum",
        "zhparser",
        "ip4r",
        "chkpass",
        "tsvector2",
        "pg_stat_kcache",
        "wal2json",
        "pg_repack",
        "plperl",
        "plperlu",
        "plpython3u",
        "plpython",
        "pltcl",
        "pltclu",
        "pljava",
        "plrust",
        "aws_commons",
        "aws_s3",
        "h3",
        "pg_graphql",
        "pg_net",
        "pgjwt",
        "moddatetime",
        "age",
        "semver",
        "rdkit",
        "vector",
        "citus",
        "pg_tle",
        "pg_roaringbitmap",
        "pg_ivm",
        "pg_rational",
        "pg_partman_bgw",
        "q3c",
        "pg_track_settings",
        "pg_variables",
        "pg_walinspect",
        "pgmq",
        "address_standardizer",
        "address_standardizer_data_us",
        "postgis_raster",
        "postgis_sfcgal",
        "postgis_tiger_geocoder",
        "postgis_topology",
    }

    def __init__(self, sql_driver: SqlDriver, timeout: Optional[float] = None) -> None:
        """
        Описание метода __init__:
        Инициализирует SafeSqlDriver с базовым SQL-драйвером и опциональным таймаутом.

        Аргументы:
            sql_driver (SqlDriver): Базовый SQL-драйвер для обертки
            timeout (Optional[float]): Опциональный таймаут в секундах для выполнения запросов

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver
        self.timeout: Optional[float] = timeout

    def _validate_node(self, node: Node) -> None:
        """
        Описание метода _validate_node:
        Рекурсивно валидирует узел AST и все его дочерние узлы.

        Аргументы:
            node (Node): Узел AST для проверки

        Возвращает:
            None

        Исключения:
            ValueError: Если узел или его содержимое не разрешены
        """
        # Проверка типа узла
        if not isinstance(node, tuple(self.ALLOWED_NODE_TYPES)):
            raise ValueError(f"Тип узла {type(node).__name__} не разрешен")

        # Валидация LIKE-выражений
        if isinstance(node, A_Expr) and node.kind in (A_Expr_Kind.AEXPR_LIKE, A_Expr_Kind.AEXPR_ILIKE):
            if isinstance(node.rexpr, A_Const) and hasattr(node.rexpr.val, "sval") and node.rexpr.val.sval is not None:
                pass  # Валидация LIKE-шаблонов пока не требуется
            else:
                raise ValueError("Шаблон LIKE должен быть константной строкой")

        # Валидация вызовов функций
        if isinstance(node, FuncCall):
            func_name: str = ".".join([str(n.sval) for n in node.funcname]).lower() if node.funcname else ""
            match = self.PG_CATALOG_PATTERN.match(func_name)
            unqualified_name: str = match.group(1) if match else func_name
            if unqualified_name not in self.ALLOWED_FUNCTIONS:
                raise ValueError(f"Функция {func_name} не разрешена")

        # Запрет SELECT с locking clauses
        if isinstance(node, SelectStmt) and getattr(node, "lockingClause", None):
            raise ValueError("Locking clause в SELECT запрещена")

        # Запрет EXPLAIN ANALYZE
        if isinstance(node, ExplainStmt):
            for option in node.options or []:
                if isinstance(option, DefElem) and option.defname == "analyze":
                    raise ValueError("EXPLAIN ANALYZE не поддерживается")

        # Валидация CREATE EXTENSION
        if isinstance(node, CreateExtensionStmt):
            if node.extname not in self.ALLOWED_EXTENSIONS:
                raise ValueError(f"CREATE EXTENSION {node.extname} не поддерживается")

        # Рекурсивная валидация всех атрибутов узла
        for attr_name in node.__slots__:
            if attr_name.startswith("_"):
                continue
            try:
                attr = getattr(node, attr_name)
            except AttributeError:
                continue

            if isinstance(attr, list):
                for item in attr:
                    if isinstance(item, Node):
                        self._validate_node(item)
            elif isinstance(attr, tuple):
                for item in attr:
                    if isinstance(item, Node):
                        self._validate_node(item)
            elif isinstance(attr, Node):
                self._validate_node(attr)

    def _validate(self, query: str) -> None:
        """
        Описание метода _validate:
        Валидирует SQL-запрос, проверяя его безопасность для выполнения.

        Аргументы:
            query (str): SQL-запрос для проверки

        Возвращает:
            None

        Исключения:
            ValueError: Если запрос не прошел валидацию
        """
        try:
            parsed = pglast.parse_sql(query)
            for stmt in parsed:
                if isinstance(stmt, RawStmt):
                    if not isinstance(stmt.stmt, tuple(self.ALLOWED_STMT_TYPES)):
                        raise ValueError(f"Разрешены только SELECT, ANALYZE, VACUUM, EXPLAIN, SHOW и другие read-only запросы. Получено: {stmt.stmt}")
                else:
                    if not isinstance(stmt, tuple(self.ALLOWED_STMT_TYPES)):
                        raise ValueError(f"Разрешены только SELECT, ANALYZE, VACUUM, EXPLAIN, SHOW и другие read-only запросы. Получено: {stmt}")
                self._validate_node(stmt)
        except pglast.parser.ParseError as e:
            raise ValueError("Не удалось разобрать SQL-запрос") from e
        except Exception as e:
            raise ValueError(f"Ошибка при валидации запроса: {query}") from e

    async def execute_query(
        self,
        query: LiteralString,
        params: Optional[List[Any]] = None,
        force_readonly: bool = True,
    ) -> Optional[List[SqlDriver.RowResult]]:
        """
        Описание метода execute_query:
        Выполняет запрос после валидации его безопасности.

        Аргументы:
            query (LiteralString): SQL-запрос для выполнения
            params (Optional[List[Any]]): Параметры запроса
            force_readonly (bool): Принудительно устанавливает режим read-only (игнорируется, всегда True)

        Возвращает:
            Optional[List[SqlDriver.RowResult]]: Результаты выполнения запроса или None

        Исключения:
            ValueError: Если запрос не прошел валидацию или превысил таймаут
        """
        self._validate(query)

        # Всегда принудительно устанавливаем force_readonly=True
        query_with_comment: str = f"/* crystaldba */ {query}"
        if self.timeout:
            try:
                async with asyncio.timeout(self.timeout):
                    return await self.sql_driver.execute_query(
                        query_with_comment,
                        params=params,
                        force_readonly=True,
                    )
            except asyncio.TimeoutError as e:
                logger.warning(f"Выполнение запроса превысило таймаут {self.timeout} секунд: {query[:100]}...")
                raise ValueError(
                    f"Выполнение запроса превысило таймаут {self.timeout} секунд в ограниченном режиме. Упростите запрос или увеличьте таймаут."
                ) from e
            except Exception as e:
                logger.error(f"Ошибка при выполнении запроса: {e}")
                raise
        else:
            return await self.sql_driver.execute_query(
                query_with_comment,
                params=params,
                force_readonly=True,
            )

    @staticmethod
    def sql_to_query(sql: Composable) -> str:
        """
        Описание метода sql_to_query:
        Преобразует объект Composable в строку запроса.

        Аргументы:
            sql (Composable): SQL-объект для преобразования

        Возвращает:
            str: Строковое представление запроса
        """
        return sql.as_string()

    @staticmethod
    def param_sql_to_query(query: str, params: List[Any]) -> str:
        """
        Описание метода param_sql_to_query:
        Преобразует SQL-запрос с параметрами в строку запроса.

        Аргументы:
            query (str): SQL-запрос с заполнителями
            params (List[Any]): Список параметров

        Возвращает:
            str: Запрос с подставленными параметрами
        """
        sql_params: List[Union[Composable, Literal]] = [p if isinstance(p, Composable) else Literal(p) for p in params]
        return SafeSqlDriver.sql_to_query(SQL(query).format(*sql_params))

    @staticmethod
    async def execute_param_query(
        sql_driver: SqlDriver, query: LiteralString, params: Optional[List[Any]] = None
    ) -> Optional[List[SqlDriver.RowResult]]:
        """
        Описание метода execute_param_query:
        Выполняет параметризованный запрос после его валидации.

        Аргументы:
            sql_driver (SqlDriver): SQL-драйвер для выполнения запроса
            query (LiteralString): SQL-запрос с заполнителями
            params (Optional[List[Any]]): Параметры запроса

        Возвращает:
            Optional[List[SqlDriver.RowResult]]: Результаты выполнения запроса или None
        """
        if params:
            query_params: str = SafeSqlDriver.param_sql_to_query(query, params)
            return await sql_driver.execute_query(query_params)
        else:
            return await sql_driver.execute_query(query)
