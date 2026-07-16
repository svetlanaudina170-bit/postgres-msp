# =========================================================================
# VERSION: 1.0.0
# Path: src/postgres_mcp/sql_editor/templates.py
# SQL-шаблоны (сниппеты) для быстрой вставки
# =========================================================================
# Каждый шаблон: (название, описание, SQL-шаблон с {schema}/{table} плэйсхолдерами)

SQL_TEMPLATES: list[dict[str, str]] = [
    {
        "name": "Row count",
        "desc": "Count rows in table",
        "sql": "SELECT count(*) AS row_count\nFROM {schema}.{table};",
    },
    {
        "name": "All columns (LIMIT 100)",
        "desc": "SELECT * with limit",
        "sql": "SELECT *\nFROM {schema}.{table}\nLIMIT 100;",
    },
    {
        "name": "Column list",
        "desc": "List columns with types",
        "sql": "SELECT\n  column_name,\n  data_type,\n  is_nullable,\n  column_default\nFROM information_schema.columns\nWHERE table_schema = '{schema}'\n  AND table_name = '{table}'\nORDER BY ordinal_position;",
    },
    {
        "name": "Table size",
        "desc": "Table + indexes + total size",
        "sql": "SELECT\n  pg_size_pretty(pg_table_size('{schema}.{table}')) AS table_size,\n  pg_size_pretty(pg_indexes_size('{schema}.{table}')) AS indexes_size,\n  pg_size_pretty(pg_total_relation_size('{schema}.{table}')) AS total_size;",
    },
    {
        "name": "List indexes",
        "desc": "All indexes on the table",
        "sql": "SELECT\n  indexname,\n  indexdef\nFROM pg_indexes\nWHERE schemaname = '{schema}'\n  AND tablename = '{table}'\nORDER BY indexname;",
    },
    {
        "name": "Unused indexes",
        "desc": "Indexes with low scan count",
        "sql": "SELECT\n  schemaname,\n  tablename,\n  indexname,\n  idx_scan,\n  pg_size_pretty(pg_relation_size(indexrelid)) AS index_size\nFROM pg_stat_user_indexes\nWHERE idx_scan < 10\n  AND schemaname NOT IN ('pg_catalog', 'information_schema')\nORDER BY idx_scan ASC;",
    },
    {
        "name": "Duplicate indexes",
        "desc": "Potentially duplicate indexes",
        "sql": "SELECT\n  a.indexname,\n  a.tablename,\n  a.indexdef\nFROM pg_indexes a\nJOIN pg_indexes b\n  ON a.schemaname = b.schemaname\n AND a.tablename = b.tablename\n AND a.indexname != b.indexname\nWHERE a.schemaname = '{schema}'\nORDER BY a.tablename, a.indexname;",
    },
    {
        "name": "Active queries",
        "desc": "Currently running queries",
        "sql": "SELECT\n  pid,\n  state,\n  now() - pg_stat_activity.query_start AS duration,\n  substring(query, 1, 100) AS query_short\nFROM pg_stat_activity\nWHERE state != 'idle'\n  AND query NOT LIKE '%pg_stat_activity%'\nORDER BY query_start DESC;",
    },
    {
        "name": "Table bloat",
        "desc": "Estimated table bloat",
        "sql": "SELECT\n  schemaname,\n  tablename,\n  n_live_tup,\n  n_dead_tup,\n  round(n_dead_tup * 100.0 / NULLIF(n_live_tup + n_dead_tup, 0), 1) AS dead_pct\nFROM pg_stat_user_tables\nWHERE n_dead_tup > 1000\nORDER BY n_dead_tup DESC;",
    },
    {
        "name": "Locking queries",
        "desc": "Queries waiting on locks",
        "sql": "SELECT\n  blocked.pid AS blocked_pid,\n  blocked.query AS blocked_query,\n  blocking.pid AS blocking_pid,\n  blocking.query AS blocking_query\nFROM pg_stat_activity blocked\nJOIN pg_stat_activity blocking ON (\n  SELECT pid FROM pg_blocking_pids(blocked.pid)\n) = blocking.pid\nWHERE blocked.state = 'active'\n  AND blocked.wait_event_type = 'Lock';",
    },
    {
        "name": "FK relationships",
        "desc": "Foreign keys for table",
        "sql": "SELECT\n  tc.constraint_name,\n  kcu.column_name,\n  ccu.table_schema AS foreign_schema,\n  ccu.table_name AS foreign_table,\n  ccu.column_name AS foreign_column\nFROM information_schema.table_constraints tc\nJOIN information_schema.key_column_usage kcu\n  ON tc.constraint_name = kcu.constraint_name\nJOIN information_schema.constraint_column_usage ccu\n  ON tc.constraint_name = ccu.constraint_name\nWHERE tc.constraint_type = 'FOREIGN KEY'\n  AND tc.table_schema = '{schema}'\n  AND tc.table_name = '{table}';",
    },
    {
        "name": "Find duplicates",
        "desc": "Find duplicate rows (by all columns)",
        "sql": "SELECT *, count(*) AS cnt\nFROM {schema}.{table}\nGROUP BY {schema}.{table}.*\nHAVING count(*) > 1;",
    },
    {
        "name": "Vacuum info",
        "desc": "Last vacuum/analyze times",
        "sql": "SELECT\n  schemaname,\n  tablename,\n  last_vacuum,\n  last_autovacuum,\n  last_analyze,\n  last_autoanalyze,\n  vacuum_count,\n  analyze_count\nFROM pg_stat_user_tables\nWHERE schemaname = '{schema}'\n  AND tablename = '{table}';",
    },
    {
        "name": "Database size",
        "desc": "Size of all databases",
        "sql": "SELECT\n  datname,\n  pg_size_pretty(pg_database_size(datname)) AS size\nFROM pg_database\nORDER BY pg_database_size(datname) DESC;",
    },
    {
        "name": "Top queries by time",
        "desc": "Requires pg_stat_statements",
        "sql": "SELECT\n  query,\n  calls,\n  round(total_exec_time::numeric, 1) AS total_ms,\n  round(mean_exec_time::numeric, 1) AS avg_ms,\n  round((100 * total_exec_time / sum(total_exec_time) OVER ())::numeric, 1) AS pct\nFROM pg_stat_statements\nWHERE query NOT LIKE '%pg_stat_statements%'\nORDER BY total_exec_time DESC\nLIMIT 10;",
    },
    {
        "name": "Extensions",
        "desc": "Installed extensions",
        "sql": "SELECT\n  extname,\n  extversion,\n  extnamespace::regnamespace::text AS schema\nFROM pg_extension\nORDER BY extname;",
    },
]


def get_template_by_name(name: str) -> dict | None:
    for t in SQL_TEMPLATES:
        if t["name"] == name:
            return t
    return None


def template_names() -> list[str]:
    return [t["name"] for t in SQL_TEMPLATES]


def apply_template(template: dict, schema: str = "public", table: str = "") -> str:
    sql = template["sql"]
    sql = sql.replace("{schema}", schema or "public")
    sql = sql.replace("{table}", table or "my_table")
    return sql
