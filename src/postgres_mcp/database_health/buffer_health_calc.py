# Анализ файла buffer_health_calc.py
#
# Описание файла:
# Файл buffer_health_calc.py содержит класс BufferHealthCalc, который предоставляет функциональность
# для оценки производительности кэша PostgreSQL, включая показатели hit rate для индексов и таблиц.
# Класс использует статистику из представлений pg_statio_user_indexes и pg_statio_user_tables для
# расчета процента попаданий в кэш и сравнения с заданным порогом.
#
# Используемые модули:
# - typing: для аннотаций типов
#
# Импорты из пакета:
# - SqlDriver: для взаимодействия с базой данных PostgreSQL
#
# Основные компоненты:
# - Класс BufferHealthCalc: основной класс для расчета показателей hit rate кэша
#
# Зависимости:
# Файл зависит от модуля sql (sql_driver.py), который предоставляет интерфейс SqlDriver.
# Требуется доступ к статистическим представлениям PostgreSQL (pg_statio_user_indexes, pg_statio_user_tables).
#
# Примечания:
# - Поле _cached_indexes не используется в текущей реализации, но определено как классовая переменная.
# - Результаты возвращаются в виде строк для удобства представления.

from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..sql import SqlDriver


# Описание класса BufferHealthCalc
#
# Класс BufferHealthCalc предоставляет методы для расчета показателей hit rate кэша для индексов
# и таблиц в PostgreSQL. Он анализирует статистику кэша и возвращает текстовое описание
# с процентом попаданий и сравнением с заданным порогом.
class BufferHealthCalc:
    """Калькулятор состояния кэша PostgreSQL."""

    _cached_indexes: Optional[List[Dict[str, Any]]] = None

    def __init__(self, sql_driver: SqlDriver) -> None:
        """
        Описание метода __init__:
        Инициализирует объект BufferHealthCalc.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для взаимодействия с базой данных

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver

    async def index_hit_rate(self, threshold: float = 0.95) -> str:
        """
        Описание метода index_hit_rate:
        Рассчитывает процент попаданий в кэш для индексов.

        Аргументы:
            threshold (float): Пороговое значение для сравнения (по умолчанию 0.95)

        Возвращает:
            str: Текстовое описание процента попаданий в кэш и сравнения с порогом
        """
        result = await self.sql_driver.execute_query("""
            SELECT
                (sum(idx_blks_hit)) / nullif(sum(idx_blks_hit + idx_blks_read), 0) AS rate
            FROM
                pg_statio_user_indexes
        """)

        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result] if result else []

        if not result_list or result_list[0]["rate"] is None:
            return "Статистика кэша индексов недоступна."

        hit_rate: float = float(result_list[0]["rate"]) * 100
        threshold_pct: float = threshold * 100

        if hit_rate >= threshold_pct:
            return f"Процент попаданий в кэш индексов: {hit_rate:.1f}% (выше порога {threshold_pct:.1f}%)"
        return f"Процент попаданий в кэш индексов: {hit_rate:.1f}% (ниже порога {threshold_pct:.1f}%)"

    async def table_hit_rate(self, threshold: float = 0.95) -> str:
        """
        Описание метода table_hit_rate:
        Рассчитывает процент попаданий в кэш для таблиц.

        Аргументы:
            threshold (float): Пороговое значение для сравнения (по умолчанию 0.95)

        Возвращает:
            str: Текстовое описание процента попаданий в кэш и сравнения с порогом
        """
        result = await self.sql_driver.execute_query("""
            SELECT
                sum(heap_blks_hit) / nullif(sum(heap_blks_hit + heap_blks_read), 0) AS rate
            FROM
                pg_statio_user_tables
        """)

        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result] if result else []

        if not result_list or result_list[0]["rate"] is None:
            return "Статистика кэша таблиц недоступна."

        hit_rate: float = float(result_list[0]["rate"]) * 100
        threshold_pct: float = threshold * 100

        if hit_rate >= threshold_pct:
            return f"Процент попаданий в кэш таблиц: {hit_rate:.1f}% (выше порога {threshold_pct:.1f}%)"
        return f"Процент попаданий в кэш таблиц: {hit_rate:.1f}% (ниже порога {threshold_pct:.1f}%)"
