# Анализ файла sequence_health_calc.py
#
# Описание файла:
# Файл sequence_health_calc.py содержит класс SequenceHealthCalc, который предоставляет функциональность
# для проверки состояния последовательностей (sequences) в базе данных PostgreSQL. Класс анализирует
# использование последовательностей, используемых в качестве значений по умолчанию для столбцов,
# и определяет, насколько близко они находятся к своему максимальному значению. Также включает
# класс данных SequenceMetrics для хранения метрик последовательностей.
#
# Используемые модули:
# - dataclasses: для создания классов данных
# - typing: для аннотаций типов
# - psycopg.sql: для безопасной работы с SQL-идентификаторами
#
# Импорты из пакета:
# - SafeSqlDriver, SqlDriver: для безопасного выполнения SQL-запросов и взаимодействия с базой данных
#
# Основные компоненты:
# - Класс SequenceMetrics: класс данных для хранения метрик последовательностей
# - Класс SequenceHealthCalc: основной класс для проверки состояния последовательностей
#
# Зависимости:
# Файл зависит от модуля sql (sql_driver.py, safe_sql.py) и библиотеки psycopg.
# Требуется доступ к системным каталогам PostgreSQL (pg_catalog.pg_attribute, pg_catalog.pg_class и др.).
#
# Примечания:
# - Класс использует асинхронные методы для выполнения запросов к базе данных.
# - Порог использования последовательности (threshold) задается при инициализации.
# - Результаты возвращаются в виде строк для удобства представления.

from dataclasses import dataclass
from typing import List, Dict, Any, Tuple

from psycopg.sql import Identifier

from ..sql import SafeSqlDriver, SqlDriver

# Описание класса SequenceMetrics
#
# Класс SequenceMetrics — это класс данных, который хранит метрики последовательностей,
# включая схему, таблицу, столбец, имя последовательности, тип столбца и информацию
# об использовании последовательности.
@dataclass
class SequenceMetrics:
    """Метрики последовательностей базы данных."""

    schema: str
    table: str
    column: str
    sequence: str
    column_type: str
    last_value: int
    max_value: int
    is_healthy: bool
    readable: bool = True

    @property
    def percent_used(self) -> float:
        """
        Описание свойства percent_used:
        Вычисляет процент использования последовательности.

        Возвращает:
            float: Процент использованных значений последовательности
        """
        return (self.last_value / self.max_value) * 100 if self.max_value else 0

# Описание класса SequenceHealthCalc
#
# Класс SequenceHealthCalc предоставляет методы для проверки состояния последовательностей
# в базе данных PostgreSQL, определяя, насколько они близки к своему максимальному значению.
class SequenceHealthCalc:
    """Калькулятор состояния последовательностей базы данных PostgreSQL."""

    def __init__(self, sql_driver: SqlDriver, threshold: float = 0.9) -> None:
        """
        Описание метода __init__:
        Инициализирует объект SequenceHealthCalc.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для взаимодействия с базой данных
            threshold (float): Порог использования последовательности (по умолчанию 0.9)

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver
        self.threshold: float = threshold

    async def sequence_danger_check(self) -> str:
        """
        Описание метода sequence_danger_check:
        Проверяет, приближаются ли последовательности к своим максимальным значениям.

        Возвращает:
            str: Текстовое описание состояния последовательностей
        """
        metrics: List[SequenceMetrics] = await self._get_sequence_metrics()

        if not metrics:
            return "Последовательности в базе данных не найдены."

        metrics.sort(key=lambda x: x.max_value - x.last_value)
        unhealthy: List[SequenceMetrics] = [m for m in metrics if not m.is_healthy]

        if not unhealthy:
            return "Все последовательности находятся в пределах нормального использования."

        result: List[str] = ["Последовательности, приближающиеся к максимальному значению:"]
        for metric in unhealthy:
            remaining: int = metric.max_value - metric.last_value
            result.append(
                f"Последовательность '{metric.schema}.{metric.sequence}', используемая для {metric.table}.{metric.column}, "
                f"использовала {metric.percent_used:.1f}% доступных значений "
                f"({metric.last_value:,} из {metric.max_value:,}, осталось {remaining:,})"
            )
        return "\n".join(result)

    async def _get_sequence_metrics(self) -> List[SequenceMetrics]:
        """
        Описание метода _get_sequence_metrics:
        Получает метрики для последовательностей в базе данных.

        Возвращает:
            List[SequenceMetrics]: Список объектов SequenceMetrics с информацией о последовательностях
        """
        sequences = await self.sql_driver.execute_query("""
            SELECT
                n.nspname AS table_schema,
                c.relname AS table,
                attname AS column,
                format_type(a.atttypid, a.atttypmod) AS column_type,
                pg_get_expr(d.adbin, d.adrelid) AS default_value
            FROM
                pg_catalog.pg_attribute a
            INNER JOIN
                pg_catalog.pg_class c ON c.oid = a.attrelid
            INNER JOIN
                pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            INNER JOIN
                pg_catalog.pg_attrdef d ON (a.attrelid, a.attnum) = (d.adrelid, d.adnum)
            WHERE
                NOT a.attisdropped
                AND a.attnum > 0
                AND pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval%'
                AND n.nspname NOT LIKE 'pg\\_temp\\_%'
        """)

        if not sequences:
            return []

        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in sequences]
        sequence_metrics: List[SequenceMetrics] = []

        for seq in result_list:
            schema, sequence = self._parse_sequence_name(seq["default_value"])
            if not sequence:
                continue

            max_value: int = 2147483647 if seq["column_type"] == "integer" else 9223372036854775807
            attrs = await SafeSqlDriver.execute_param_query(
                self.sql_driver,
                """
                SELECT
                    has_sequence_privilege('{}', 'SELECT') AS readable,
                    last_value
                FROM {}
                """,
                [Identifier(schema, sequence), Identifier(schema, sequence)],
            )

            if not attrs:
                continue

            result_list_attrs: List[Dict[str, Any]] = [dict(x.cells) for x in attrs]
            attr: Dict[str, Any] = result_list_attrs[0]
            sequence_metrics.append(
                SequenceMetrics(
                    schema=schema,
                    table=seq["table"],
                    column=seq["column"],
                    sequence=sequence,
                    column_type=seq["column_type"],
                    last_value=attr["last_value"],
                    max_value=max_value,
                    readable=attr["readable"],
                    is_healthy=attr["last_value"] / max_value <= self.threshold,
                )
            )

        return sequence_metrics

    def _parse_sequence_name(self, default_value: str) -> Tuple[str, str]:
        """
        Описание метода _parse_sequence_name:
        Извлекает имя схемы и последовательности из выражения значения по умолчанию.

        Аргументы:
            default_value (str): Выражение значения по умолчанию (например, nextval('id_seq'::regclass))

        Возвращает:
            Tuple[str, str]: Кортеж из имени схемы и имени последовательности
        """
        clean_value: str = default_value.replace("nextval('", "").replace("'::regclass)", "").replace("('", "").replace("'::text)", "")
        parts: List[str] = clean_value.split(".")
        return ("public", parts[0]) if len(parts) == 1 else (parts[0], parts[1])