# Анализ файла constraint_health_calc.py
#
# Описание файла:
# Файл constraint_health_calc.py содержит класс ConstraintHealthCalc, который предоставляет функциональность
# проверки состояния ограничений (constraints) в базе данных PostgreSQL. Класс анализирует недействительные
# ограничения, используя статистику из системных каталогов PostgreSQL, и предоставляет информацию о них.
# Также включает класс данных ConstraintMetrics для хранения метрик ограничений.
#
# Используемые модули:
# - dataclasses: для создания классов данных
# - typing: для аннотаций типов
#
# Импорты из пакета:
# - SqlDriver: для взаимодействия с базой данных PostgreSQL
#
# Основные компоненты:
# - Класс ConstraintMetrics: класс данных для хранения метрик ограничений
# - Класс ConstraintHealthCalc: основной класс для проверки состояния ограничений
#
# Зависимости:
# Файл зависит от модуля sql (sql_driver.py), который предоставляет интерфейс SqlDriver.
# Требуется доступ к системным каталогам PostgreSQL (pg_catalog.pg_constraint, information_schema.table_constraints).
#
# Примечания:
# - Класс использует асинхронные методы для выполнения запросов к базе данных.
# - Результаты возвращаются в виде строк или списков объектов для удобства представления.

from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from ..sql import SqlDriver

# Описание класса ConstraintMetrics
#
# Класс ConstraintMetrics — это класс данных, который хранит метрики ограничений,
# включая схему, таблицу, имя ограничения и информацию о связанных таблицах.
@dataclass
class ConstraintMetrics:
    """Метрики ограничений базы данных."""

    schema: str
    table: str
    name: str
    referenced_schema: Optional[str]
    referenced_table: Optional[str]

# Описание класса ConstraintHealthCalc
#
# Класс ConstraintHealthCalc предоставляет методы для проверки состояния ограничений
# в базе данных PostgreSQL, включая выявление недействительных ограничений и подсчет
# общего и активных ограничений.
class ConstraintHealthCalc:
    """Калькулятор состояния ограничений базы данных PostgreSQL."""

    def __init__(self, sql_driver: SqlDriver) -> None:
        """
        Описание метода __init__:
        Инициализирует объект ConstraintHealthCalc.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для взаимодействия с базой данных

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver

    async def invalid_constraints_check(self) -> str:
        """
        Описание метода invalid_constraints_check:
        Проверяет наличие недействительных ограничений в базе данных.

        Возвращает:
            str: Текстовое описание найденных недействительных ограничений
        """
        metrics: List[ConstraintMetrics] = await self._get_invalid_constraints()

        if not metrics:
            return "Недействительные ограничения не найдены."

        result: List[str] = ["Найдены недействительные ограничения:"]
        for metric in metrics:
            if metric.referenced_table:
                result.append(
                    f"Ограничение '{metric.name}' в таблице '{metric.schema}.{metric.table}' "
                    f"ссылается на '{metric.referenced_schema}.{metric.referenced_table}' и является недействительным"
                )
            else:
                result.append(
                    f"Ограничение '{metric.name}' в таблице '{metric.schema}.{metric.table}' является недействительным"
                )
        return "\n".join(result)

    async def _get_invalid_constraints(self) -> List[ConstraintMetrics]:
        """
        Описание метода _get_invalid_constraints:
        Получает все недействительные ограничения в базе данных.

        Возвращает:
            List[ConstraintMetrics]: Список объектов ConstraintMetrics с информацией о недействительных ограничениях
        """
        results = await self.sql_driver.execute_query("""
            SELECT
                nsp.nspname AS schema,
                rel.relname AS table,
                con.conname AS name,
                fnsp.nspname AS referenced_schema,
                frel.relname AS referenced_table
            FROM
                pg_catalog.pg_constraint con
            INNER JOIN
                pg_catalog.pg_class rel ON rel.oid = con.conrelid
            LEFT JOIN
                pg_catalog.pg_class frel ON frel.oid = con.confrelid
            LEFT JOIN
                pg_catalog.pg_namespace nsp ON nsp.oid = con.connamespace
            LEFT JOIN
                pg_catalog.pg_namespace fnsp ON fnsp.oid = frel.relnamespace
            WHERE
                con.convalidated = 'f'
        """)

        if not results:
            return []

        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in results]

        return [
            ConstraintMetrics(
                schema=row["schema"],
                table=row["table"],
                name=row["name"],
                referenced_schema=row["referenced_schema"],
                referenced_table=row["referenced_table"],
            )
            for row in result_list
        ]

    async def _get_total_constraints(self) -> int:
        """
        Описание метода _get_total_constraints:
        Получает общее количество ограничений в базе данных.

        Возвращает:
            int: Общее количество ограничений
        """
        result = await self.sql_driver.execute_query("""
            SELECT COUNT(*) as count
            FROM information_schema.table_constraints
        """)
        if not result:
            return 0
        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result]
        return result_list[0]["count"] if result_list else 0

    async def _get_active_constraints(self) -> int:
        """
        Описание метода _get_active_constraints:
        Получает количество активных (не отложенных) ограничений в базе данных.

        Возвращает:
            int: Количество активных ограничений
        """
        result = await self.sql_driver.execute_query("""
            SELECT COUNT(*) as count
            FROM information_schema.table_constraints
            WHERE is_deferrable = 'NO'
        """)
        if not result:
            return 0
        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result]
        return result_list[0]["count"] if result_list else 0