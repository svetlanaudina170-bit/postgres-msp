# Анализ файла index_health_calc.py
#
# Описание файла:
# Файл index_health_calc.py содержит класс IndexHealthCalc, который предоставляет функциональность
# для проверки состояния индексов в базе данных PostgreSQL. Класс выполняет проверки на наличие
# недействительных индексов, дублирующихся индексов, раздувания (bloat) индексов и редко используемых
# индексов. Он использует системные каталоги PostgreSQL (pg_index, pg_stat_user_indexes и др.)
# для сбора информации и предоставляет результаты в виде текстовых описаний.
#
# Используемые модули:
# - typing: для аннотаций типов
#
# Импорты из пакета:
# - SafeSqlDriver, SqlDriver: для безопасного выполнения SQL-запросов и взаимодействия с базой данных
#
# Основные компоненты:
# - Класс IndexHealthCalc: основной класс для проверки состояния индексов
#
# Зависимости:
# Файл зависит от модуля sql (sql_driver.py, safe_sql.py), который предоставляет интерфейсы
# SqlDriver и SafeSqlDriver. Требуется доступ к системным каталогам PostgreSQL.
#
# Примечания:
# - Поле _cached_indexes используется для кэширования информации об индексах, чтобы избежать
#   повторных запросов к базе данных.
# - Класс использует асинхронные методы для выполнения запросов к базе данных.
# - Результаты возвращаются в виде строк для удобства представления.

from typing import Any, Dict, List, Optional

from ..sql import SafeSqlDriver, SqlDriver

# Описание класса IndexHealthCalc
#
# Класс IndexHealthCalc предоставляет методы для анализа состояния индексов в базе данных
# PostgreSQL, включая проверки на недействительные, дублирующиеся, раздутые и редко используемые индексы.
class IndexHealthCalc:
    """Калькулятор состояния индексов базы данных PostgreSQL."""

    _cached_indexes: Optional[List[Dict[str, Any]]] = None

    def __init__(self, sql_driver: SqlDriver) -> None:
        """
        Описание метода __init__:
        Инициализирует объект IndexHealthCalc.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для взаимодействия с базой данных

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver

    async def invalid_index_check(self) -> str:
        """
        Описание метода invalid_index_check:
        Проверяет наличие недействительных индексов в базе данных.

        Возвращает:
            str: Текстовое описание найденных недействительных индексов
        """
        indexes: List[Dict[str, Any]] = await self._indexes()
        invalid_indexes: List[Dict[str, Any]] = [idx for idx in indexes if not idx["valid"]]
        if not invalid_indexes:
            return "Недействительные индексы не найдены."

        return "Найдены недействительные индексы: " + "\n".join(
            [f"Индекс '{idx['name']}' на таблице '{idx['table']}' недействителен." for idx in invalid_indexes]
        )

    async def duplicate_index_check(self) -> str:
        """
        Описание метода duplicate_index_check:
        Проверяет наличие дублирующихся индексов в базе данных.

        Возвращает:
            str: Текстовое описание найденных дублирующихся индексов
        """
        indexes: List[Dict[str, Any]] = await self._indexes()
        dup_indexes: List[Dict[str, Any]] = []

        indexes_by_table: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
        for idx in indexes:
            key = (idx["schema"], idx["table"])
            if key not in indexes_by_table:
                indexes_by_table[key] = []
            indexes_by_table[key].append(idx)

        for index in [i for i in indexes if i["valid"] and not i["primary"] and not i["unique"]]:
            table_indexes = indexes_by_table[(index["schema"], index["table"])]
            for covering_idx in table_indexes:
                if (
                    covering_idx["valid"]
                    and covering_idx["name"] != index["name"]
                    and self._index_covers(covering_idx["columns"], index["columns"])
                    and covering_idx["using"] == index["using"]
                    and covering_idx["indexprs"] == index["indexprs"]
                    and covering_idx["indpred"] == index["indpred"]
                ):
                    if (
                        covering_idx["columns"] != index["columns"]
                        or index["name"] > covering_idx["name"]
                        or covering_idx["primary"]
                        or covering_idx["unique"]
                    ):
                        dup_indexes.append({"unneeded_index": index, "covering_index": covering_idx})
                        break

        if not dup_indexes:
            return "Дублирующиеся индексы не найдены."

        sorted_dups = sorted(
            dup_indexes,
            key=lambda x: (
                x["unneeded_index"]["table"],
                x["unneeded_index"]["columns"],
            ),
        )

        result: List[str] = ["Найдены дублирующиеся индексы:"]
        for dup in sorted_dups:
            result.append(
                f"Индекс '{dup['unneeded_index']['name']}' на таблице '{dup['unneeded_index']['table']}' "
                f"покрывается индексом '{dup['covering_index']['name']}'"
            )

        return "\n".join(result)

    async def index_bloat(self, min_size: int = 104857600) -> str:
        """
        Описание метода index_bloat:
        Проверяет наличие раздутых индексов, превышающих минимальный размер.

        Аргументы:
            min_size (int): Минимальный размер индекса в байтах для учета раздувания (по умолчанию 100MB)

        Возвращает:
            str: Текстовое описание найденных раздутых индексов
        """
        bloated_indexes = await SafeSqlDriver.execute_param_query(
            self.sql_driver,
            """
            WITH btree_index_atts AS (
                SELECT
                    nspname, relname, reltuples, relpages, indrelid, relam,
                    regexp_split_to_table(indkey::text, ' ')::smallint AS attnum,
                    indexrelid as index_oid
                FROM
                    pg_index
                JOIN
                    pg_class ON pg_class.oid = pg_index.indexrelid
                JOIN
                    pg_namespace ON pg_namespace.oid = pg_class.relnamespace
                JOIN
                    pg_am ON pg_class.relam = pg_am.oid
                WHERE
                    pg_am.amname = 'btree'
            ),
            index_item_sizes AS (
                SELECT
                    i.nspname,
                    i.relname,
                    i.reltuples,
                    i.relpages,
                    i.relam,
                    (quote_ident(s.schemaname) || '.' || quote_ident(s.tablename))::regclass AS starelid,
                    a.attrelid AS table_oid, index_oid,
                    current_setting('block_size')::numeric AS bs,
                    CASE
                        WHEN version() ~ 'mingw32' OR version() ~ '64-bit' THEN 8
                        ELSE 4
                    END AS maxalign,
                    24 AS pagehdr,
                    CASE WHEN max(coalesce(s.null_frac,0)) = 0
                        THEN 2
                        ELSE 6
                    END AS index_tuple_hdr,
                    sum( (1-coalesce(s.null_frac, 0)) * coalesce(s.avg_width, 2048) ) AS nulldatawidth
                FROM
                    pg_attribute AS a
                JOIN
                    pg_stats AS s ON (quote_ident(s.schemaname) || '.' || quote_ident(s.tablename))::regclass=a.attrelid AND s.attname = a.attname
                JOIN
                    btree_index_atts AS i ON i.indrelid = a.attrelid AND a.attnum = i.attnum
                WHERE
                    a.attnum > 0
                GROUP BY
                    1, 2, 3, 4, 5, 6, 7, 8, 9
            ),
            index_aligned AS (
                SELECT
                    maxalign,
                    bs,
                    nspname,
                    relname AS index_name,
                    reltuples,
                    relpages,
                    relam,
                    table_oid,
                    index_oid,
                    ( 2 +
                        maxalign - CASE
                            WHEN index_tuple_hdr%maxalign = 0 THEN maxalign
                            ELSE index_tuple_hdr%maxalign
                        END
                    + nulldatawidth + maxalign - CASE
                            WHEN nulldatawidth::integer%maxalign = 0 THEN maxalign
                            ELSE nulldatawidth::integer%maxalign
                        END
                    )::numeric AS nulldatahdrwidth, pagehdr
                FROM
                    index_item_sizes AS s1
            ),
            otta_calc AS (
                SELECT
                    bs,
                    nspname,
                    table_oid,
                    index_oid,
                    index_name,
                    relpages,
                    coalesce(
                        ceil((reltuples*(4+nulldatahdrwidth))/(bs-pagehdr::float)) +
                        CASE WHEN am.amname IN ('hash','btree') THEN 1 ELSE 0 END , 0
                    ) AS otta
                FROM
                    index_aligned AS s2
                LEFT JOIN
                    pg_am am ON s2.relam = am.oid
            ),
            raw_bloat AS (
                SELECT
                    nspname,
                    c.relname AS table_name,
                    index_name,
                    bs*(sub.relpages)::bigint AS totalbytes,
                    CASE
                        WHEN sub.relpages <= otta THEN 0
                        ELSE bs*(sub.relpages-otta)::bigint END
                        AS wastedbytes,
                    CASE
                        WHEN sub.relpages <= otta
                        THEN 0 ELSE bs*(sub.relpages-otta)::bigint * 100 / (bs*(sub.relpages)::bigint) END
                        AS realbloat,
                    pg_relation_size(sub.table_oid) as table_bytes,
                    stat.idx_scan as index_scans,
                    stat.indexrelid
                FROM
                    otta_calc AS sub
                JOIN
                    pg_class AS c ON c.oid=sub.table_oid
                JOIN
                    pg_stat_user_indexes AS stat ON sub.index_oid = stat.indexrelid
            )
            SELECT
                nspname AS schema,
                table_name AS table,
                index_name AS index,
                wastedbytes AS bloat_bytes,
                totalbytes AS index_bytes,
                pg_get_indexdef(rb.indexrelid) AS definition,
                indisprimary AS primary
            FROM
                raw_bloat rb
            INNER JOIN
                pg_index i ON i.indexrelid = rb.indexrelid
            WHERE
                wastedbytes >= {}
            ORDER BY
                wastedbytes DESC,
                index_name
        """,
            [min_size],
        )

        if not bloated_indexes:
            return "Раздутые индексы не найдены."

        result: List[str] = ["Найдены раздутые индексы:"]
        bloated_indexes_dicts: List[Dict[str, Any]] = [dict(idx.cells) for idx in bloated_indexes]
        for idx in bloated_indexes_dicts:
            bloat_mb: float = int(idx["bloat_bytes"]) / (1024 * 1024)
            total_mb: float = int(idx["index_bytes"]) / (1024 * 1024)
            result.append(
                f"Индекс '{idx['index']}' на таблице '{idx['table']}' имеет раздувание {bloat_mb:.1f}MB из общего размера {total_mb:.1f}MB"
            )

        return "\n".join(result)

    async def _indexes(self) -> List[Dict[str, Any]]:
        """
        Описание метода _indexes:
        Получает информацию об индексах из базы данных с кэшированием.

        Возвращает:
            List[Dict[str, Any]]: Список словарей с информацией об индексах
        """
        if self._cached_indexes:
            return self._cached_indexes

        results = await self.sql_driver.execute_query("""
            SELECT
                schemaname AS schema,
                t.relname AS table,
                ix.relname AS name,
                regexp_replace(pg_get_indexdef(i.indexrelid), '^[^\\(]*\\((.*)\\)$', '\\1') AS columns,
                regexp_replace(pg_get_indexdef(i.indexrelid), '.* USING ([^ ]*) \\(.*', '\\1') AS using,
                indisunique AS unique,
                indisprimary AS primary,
                indisvalid AS valid,
                indexprs::text,
                indpred::text,
                pg_get_indexdef(i.indexrelid) AS definition
            FROM
                pg_index i
            INNER JOIN
                pg_class t ON t.oid = i.indrelid
            INNER JOIN
                pg_class ix ON ix.oid = i.indexrelid
            LEFT JOIN
                pg_stat_user_indexes ui ON ui.indexrelid = i.indexrelid
            WHERE
                schemaname IS NOT NULL
            ORDER BY
                1, 2
        """)

        if results is None:
            return []

        indexes: List[Dict[str, Any]] = [dict(idx.cells) for idx in results]
        for idx in indexes:
            cols: str = idx["columns"]
            cols = cols.replace(") WHERE (", " WHERE ").split(", ")
            idx["columns"] = [col.strip('"') for col in cols]

        self._cached_indexes = indexes
        return indexes

    def _index_covers(self, indexed_columns: List[str], columns: List[str]) -> bool:
        """
        Описание метода _index_covers:
        Проверяет, покрывает ли один индекс другой по префиксу столбцов.

        Аргументы:
            indexed_columns (List[str]): Столбцы потенциально покрывающего индекса
            columns (List[str]): Столбцы проверяемого индекса

        Возвращает:
            bool: True, если indexed_columns покрывает columns
        """
        return indexed_columns[: len(columns)] == columns

    async def unused_indexes(self, max_scans: int = 50) -> str:
        """
        Описание метода unused_indexes:
        Проверяет наличие редко используемых индексов.

        Аргументы:
            max_scans (int): Максимальное количество сканирований для учета индекса как неиспользуемого (по умолчанию 50)

        Возвращает:
            str: Текстовое описание найденных редко используемых индексов
        """
        unused = await SafeSqlDriver.execute_param_query(
            self.sql_driver,
            """
            SELECT
                schemaname AS schema,
                relname AS table,
                indexrelname AS index,
                pg_relation_size(i.indexrelid) AS size_bytes,
                idx_scan as index_scans,
                pg_get_indexdef(i.indexrelid) AS definition,
                indisprimary AS primary
            FROM
                pg_stat_user_indexes ui
            INNER JOIN
                pg_index i ON ui.indexrelid = i.indexrelid
            WHERE
                NOT indisunique
                AND idx_scan <= {}
            ORDER BY
                pg_relation_size(i.indexrelid) DESC,
                relname ASC
        """,
            [max_scans],
        )

        if not unused:
            return "Неиспользуемые индексы не найдены."

        indexes: List[Dict[str, Any]] = [dict(idx.cells) for idx in unused]
        result: List[str] = ["Найдены редко используемые индексы:"]
        for idx in indexes:
            if idx["primary"]:
                continue
            size_mb: float = int(idx["size_bytes"]) / (1024 * 1024)
            result.append(
                f"Индекс '{idx['index']}' на таблице '{idx['table']}' был сканирован только {idx['index_scans']} раз и занимает {size_mb:.1f}MB"
            )

        return "\n".join(result)