# Анализ файла vacuum_health_calc.py
#
# Описание файла:
# Файл vacuum_health_calc.py содержит класс VacuumHealthCalc, который предназначен для проверки
# состояния транзакционных идентификаторов (transaction IDs) в базе данных PostgreSQL, чтобы
# предотвратить проблему их циклического переполнения (wraparound). Класс анализирует возраст
# транзакций (AGE) таблиц и их TOAST-таблиц, сравнивая оставшееся количество транзакций
# с заданным порогом. Также предоставляет статистику о последних операциях VACUUM.
# Включает класс данных TransactionIdMetrics для хранения метрик транзакций.
#
# Используемые модули:
# - dataclasses: для создания классов данных
# - typing: для аннотаций типов
#
# Импорты из пакета:
# - SafeSqlDriver, SqlDriver: для безопасного выполнения SQL-запросов и взаимодействия с базой данных
#
# Основные компоненты:
# - Класс TransactionIdMetrics: класс данных для хранения метрик транзакционных идентификаторов
# - Класс VacuumHealthCalc: основной класс для проверки состояния транзакций
#
# Зависимости:
# Файл зависит от модуля sql (sql_driver.py, safe_sql.py).
# Требуется доступ к системным каталогам PostgreSQL (pg_class, pg_stat_user_tables) и статистике.
#
# Примечания:
# - Класс использует асинхронные методы для выполнения запросов к базе данных.
# - Порог (threshold) и максимальное значение (max_value) задаются при инициализации.
# - Результаты возвращаются в виде строк для удобства представления.

from dataclasses import dataclass
from typing import List, Dict, Any, Union

from ..sql import SafeSqlDriver, SqlDriver

# Описание класса TransactionIdMetrics
#
# Класс TransactionIdMetrics — это класс, который хранит метрики транзакционных идентификаторов
# включая таблицу, схему и т.д.
@dataclass
class TransactionIdMetrics:
    """Метрики транзакционных идентификаторов для таблиц."""

    schema: str
    table: str
    transactions_left: int
    is_healthy: bool

# Описание класса VacuumHealthCalc
#
# Класс VacuumHealthCalc предоставляет методы для анализа состояния транзакционных идентификаторов
# в базе данных PostgreSQL, выявляя таблицы, приближающиеся к циклическому переполнению.
class VacuumHealthCalc:
    """Калькулятор состояния транзакционных идентификаторов для VACUUM."""

    def __init__(
        self,
        sql_driver: SqlDriver,
        threshold: int = 10000000,
        max_value: int = 2146483648,
    ) -> None:
        """
        Описание метода __init__:
        Инициализирует объект VacuumHealthCalc.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для взаимодействия с базой данных
            threshold (int): Порог для проверки опасности переполнения (по умолчанию 10 000 000)
            max_value (int): Максимальное значение транзакционного идентификатора (по умолчанию 2,146,483,648)

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver
        self.threshold: int = threshold
        self.max_value: int = max_value

    async def transaction_id_danger_check(self) -> str:
        """
        Описание метода transaction_id_danger_check:
        Проверяет, приближаются ли таблицы к циклическому переполнению транзакционных идентификаторов.

        Возвращает:
            str: Текстовое описание состояния таблиц с риском переполнения
        """
        metrics: List[TransactionIdMetrics] = await self._get_transaction_id_metrics()

        if not metrics:
            return "Таблицы с риском переполнения транзакционных идентификаторов не найдены."

        metrics.sort(key=lambda x: x.transactions_left)
        unhealthy: List[TransactionIdMetrics] = [m for m in metrics if not m.is_healthy]

        if not unhealthy:
            return "Все таблицы имеют нормальный возраст транзакционных идентификаторов."

        result: List[str] = ["Таблицы, приближающиеся к циклическому переполнению:"]
        for metric in unhealthy:
            result.append(
                f"Таблица '{metric.schema}.{metric.table}' имеет {metric.transactions_left:,} оставшихся транзакций "
                f"до переполнения (порог: {self.threshold:,})"
            )
        return "\n".join(result)

    async def _get_transaction_id_metrics(self) -> List[TransactionIdMetrics]:
        """
        Описание метода _get_transaction_id_metrics:
        Получает метрики транзакционных идентификаторов для всех таблиц.

        Возвращает:
            List[TransactionIdMetrics]: Список объектов TransactionIdMetrics с информацией о транзакциях
        """
        results = await SafeSqlDriver.execute_param_query(
            self.sql_driver,
            """
            SELECT
                n.nspname AS schema,
                c.relname AS table,
                {} - GREATEST(AGE(c.relfrozenxid), AGE(t.relfrozenxid)) AS transactions_left
            FROM
                pg_class c
            INNER JOIN
                pg_catalog.pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN
                pg_class t ON c.reltoastrelid = t.oid
            WHERE
                c.relkind = 'r'
                AND ({} - GREATEST(AGE(c.relfrozenxid), AGE(t.relfrozenxid))) < {}
            ORDER BY
                3, 1, 2
        """,
            [self.max_value, self.max_value, self.threshold],
        )

        if not results:
            return []

        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in results]

        return [
            TransactionIdMetrics(
                schema=row["schema"],
                table=row["table"],
                transactions_left=row["transactions_left"],
                is_healthy=row["transactions_left"] >= self.threshold,
            )
            for row in result_list
        ]

    async def _get_vacuum_stats(self) -> Dict[str, Dict[str, Union[str, None]]]:
        """
        Описание метода _get_vacuum_stats:
        Получает статистику операций VACUUM для базы данных.

        Возвращает:
            Dict[str, Dict[str, Union[str, None]]]: Словарь со статистикой VACUUM для каждой таблицы
        """
        result = await self.sql_driver.execute_query("""
            SELECT relname, last_vacuum, last_autovacuum
            FROM pg_stat_user_tables
        """)
        if not result:
            return {}
        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result]
        return {
            row["relname"]: {
                "last_vacuum": row["last_vacuum"],
                "last_autovacuum": row["last_autovacuum"],
            }
            for row in result_list
        }