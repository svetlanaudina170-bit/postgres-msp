# Анализ файла connection_health_calc.py
#
# Описание файла:
# Файл connection_health_calc.py содержит класс ConnectionHealthCalc, который предоставляет функциональность
# для проверки состояния подключений к базе данных PostgreSQL. Класс анализирует общее количество подключений
# и количество простаивающих подключений (idle in transaction), сравнивая их с заданными пороговыми значениями.
# Также включает класс данных ConnectionHealthMetrics для хранения метрик состояния подключений.
#
# Используемые модули:
# - dataclasses: для создания классов данных
# - typing: для аннотаций типов
#
# Импорты из пакета:
# - SqlDriver: для взаимодействия с базой данных PostgreSQL
#
# Основные компоненты:
# - Класс ConnectionHealthMetrics: класс данных для хранения метрик состояния подключений
# - Класс ConnectionHealthCalc: основной класс для проверки состояния подключений
#
# Зависимости:
# Файл зависит от модуля sql (sql_driver.py), который предоставляет интерфейс SqlDriver.
# Требуется доступ к статистическому представлению PostgreSQL pg_stat_activity.
#
# Примечания:
# - Класс использует асинхронные методы для выполнения запросов к базе данных.
# - Результаты возвращаются в виде строк для удобства представления.

from dataclasses import dataclass
from typing import List, Dict, Any

from ..sql import SqlDriver


# Описание класса ConnectionHealthMetrics
#
# Класс ConnectionHealthMetrics — это класс данных, который хранит метрики состояния подключений,
# включая общее количество подключений, количество простаивающих подключений и их соответствие
# пороговым значениям.
@dataclass
class ConnectionHealthMetrics:
    """Метрики состояния подключений к базе данных."""

    total_connections: int
    idle_connections: int
    max_total_connections: int
    max_idle_connections: int
    is_total_connections_healthy: bool
    is_idle_connections_healthy: bool

    @property
    def is_healthy(self) -> bool:
        """
        Описание свойства is_healthy:
        Проверяет, являются ли все метрики подключений здоровыми.

        Возвращает:
            bool: True, если общее количество и простаивающие подключения в пределах нормы
        """
        return self.is_total_connections_healthy and self.is_idle_connections_healthy


# Описание класса ConnectionHealthCalc
#
# Класс ConnectionHealthCalc предоставляет методы для проверки состояния подключений
# к базе данных PostgreSQL, включая общее количество подключений и простаивающие подключения.
class ConnectionHealthCalc:
    """Калькулятор состояния подключений к базе данных PostgreSQL."""

    def __init__(
        self,
        sql_driver: SqlDriver,
        max_total_connections: int = 500,
        max_idle_connections: int = 100,
    ) -> None:
        """
        Описание метода __init__:
        Инициализирует объект ConnectionHealthCalc.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для взаимодействия с базой данных
            max_total_connections (int): Максимальное допустимое количество подключений (по умолчанию 500)
            max_idle_connections (int): Максимальное допустимое количество простаивающих подключений (по умолчанию 100)

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver
        self.max_total_connections: int = max_total_connections
        self.max_idle_connections: int = max_idle_connections

    async def total_connections_check(self) -> str:
        """
        Описание метода total_connections_check:
        Проверяет, находится ли общее количество подключений в допустимых пределах.

        Возвращает:
            str: Текстовое описание состояния общего количества подключений
        """
        total: int = await self._get_total_connections()
        if total <= self.max_total_connections:
            return f"Общее количество подключений в норме: {total}"
        return f"Высокое количество подключений: {total} (максимум: {self.max_total_connections})"

    async def idle_connections_check(self) -> str:
        """
        Описание метода idle_connections_check:
        Проверяет, находится ли количество простаивающих подключений в допустимых пределах.

        Возвращает:
            str: Текстовое описание состояния простаивающих подключений
        """
        idle: int = await self._get_idle_connections()
        if idle <= self.max_idle_connections:
            return f"Количество простаивающих подключений в норме: {idle}"
        return f"Высокое количество простаивающих подключений: {idle} (максимум: {self.max_idle_connections})"

    async def connection_health_check(self) -> str:
        """
        Описание метода connection_health_check:
        Выполняет все проверки состояния подключений и возвращает объединенные результаты.

        Возвращает:
            str: Текстовое описание общего состояния подключений
        """
        total: int = await self._get_total_connections()
        idle: int = await self._get_idle_connections()

        if total > self.max_total_connections:
            return f"Высокое количество подключений: {total}"
        elif idle > self.max_idle_connections:
            return f"Высокое количество простаивающих подключений: {idle}"
        else:
            return f"Подключения в норме: {total} всего, {idle} простаивающих"

    async def _get_total_connections(self) -> int:
        """
        Описание метода _get_total_connections:
        Получает общее количество подключений к базе данных.

        Возвращает:
            int: Общее количество подключений
        """
        result = await self.sql_driver.execute_query("""
            SELECT COUNT(*) as count
            FROM pg_stat_activity
        """)
        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result] if result else []
        return result_list[0]["count"] if result_list else 0

    async def _get_idle_connections(self) -> int:
        """
        Описание метода _get_idle_connections:
        Получает количество подключений, находящихся в состоянии простоя (idle in transaction).

        Возвращает:
            int: Количество простаивающих подключений
        """
        result = await self.sql_driver.execute_query("""
            SELECT COUNT(*) as count
            FROM pg_stat_activity
            WHERE state = 'idle in transaction'
        """)
        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result] if result else []
        return result_list[0]["count"] if result_list else 0
