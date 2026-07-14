# Анализ файла database_health.py
#
# Описание файла:
# Файл database_health.py содержит класс DatabaseHealthTool, который служит центральным инструментом для
# выполнения проверок состояния базы данных PostgreSQL. Класс поддерживает различные типы проверок,
# определенные в перечислении HealthType, и координирует работу специализированных калькуляторов
# (например, BufferHealthCalc, ConnectionHealthCalc) для анализа различных аспектов базы данных.
# Результаты возвращаются в виде строки с описанием состояния.
#
# Используемые модули:
# - __future__.annotations: для поддержки аннотаций типов в будущем стиле
# - logging: для логирования событий
# - enum: для создания перечислений
# - typing: для аннотаций типов
#
# Импорты из пакета:
# - mcp.types: для типов TextContent, ImageContent, EmbeddedResource
# - buffer_health_calc, connection_health_calc, constraint_health_calc, index_health_calc,
#   replication_calc, sequence_health_calc, vacuum_health_calc: модули с калькуляторами состояния
#
# Основные компоненты:
# - Перечисление HealthType: определяет типы проверок состояния базы данных
# - Класс DatabaseHealthTool: основной класс для выполнения проверок состояния
#
# Зависимости:
# Файл зависит от модулей mcp.types и различных калькуляторов состояния.
# Требуется доступ к базе данных PostgreSQL через SqlDriver.
#
# Примечания:
# - Класс использует асинхронные методы для выполнения проверок.
# - Тип ResponseType определен, но не используется в текущей реализации.

from __future__ import annotations

import logging
from enum import Enum
from typing import List
from typing import Set

import mcp.types as types

from .buffer_health_calc import BufferHealthCalc
from .connection_health_calc import ConnectionHealthCalc
from .constraint_health_calc import ConstraintHealthCalc
from .index_health_calc import IndexHealthCalc
from .replication_calc import ReplicationCalc
from .sequence_health_calc import SequenceHealthCalc
from .vacuum_health_calc import VacuumHealthCalc

# Определение типа ответа (не используется в текущей реализации)
ResponseType = List[types.TextContent | types.ImageContent | types.EmbeddedResource]

# Инициализация логгера
logger = logging.getLogger(__name__)


# Описание перечисления HealthType
#
# Перечисление HealthType определяет поддерживаемые типы проверок состояния базы данных.
# Поддерживает как отдельные проверки, так и проверку всех компонентов (ALL).
class HealthType(str, Enum):
    """Типы проверок состояния базы данных."""

    INDEX = "index"
    CONNECTION = "connection"
    VACUUM = "vacuum"
    SEQUENCE = "sequence"
    REPLICATION = "replication"
    BUFFER = "buffer"
    CONSTRAINT = "constraint"
    ALL = "all"


# Описание класса DatabaseHealthTool
#
# Класс DatabaseHealthTool координирует выполнение проверок состояния базы данных,
# используя специализированные калькуляторы для каждого типа проверки.
class DatabaseHealthTool:
    """Инструмент для анализа метрик состояния базы данных."""

    def __init__(self, sql_driver: types.SqlDriver) -> None:
        """
        Описание метода __init__:
        Инициализирует объект DatabaseHealthTool.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для взаимодействия с базой данных

        Возвращает:
            None
        """
        self.sql_driver: types.SqlDriver = sql_driver

    async def health(self, health_type: str) -> str:
        """
        Описание метода health:
        Выполняет проверки состояния базы данных для указанных компонентов.

        Аргументы:
            health_type (str): Список типов проверок, разделенных запятыми
                              (например, "index,connection" или "all")

        Возвращает:
            str: Текстовое описание результатов проверок

        Исключения:
            ValueError: Если переданы недопустимые типы проверок
        """
        try:
            result: str = ""
            try:
                health_types: Set[HealthType] = {HealthType(x.strip()) for x in health_type.split(",")}
            except ValueError:
                valid_types: str = ", ".join(sorted(t.value for t in HealthType))
                return (
                    f"Переданы недопустимые типы проверок: '{health_type}'. "
                    f"Допустимые значения: {valid_types}. "
                    "Пожалуйста, укажите список допустимых типов, разделенных запятыми."
                )

            if HealthType.ALL in health_types:
                health_types = set(t for t in HealthType if t != HealthType.ALL)

            if HealthType.INDEX in health_types:
                index_health = IndexHealthCalc(self.sql_driver)
                result += "Проверка недействительных индексов: " + await index_health.invalid_index_check() + "\n"
                result += "Проверка дублирующихся индексов: " + await index_health.duplicate_index_check() + "\n"
                result += "Раздувание индексов: " + await index_health.index_bloat() + "\n"
                result += "Проверка неиспользуемых индексов: " + await index_health.unused_indexes() + "\n"

            if HealthType.CONNECTION in health_types:
                connection_health = ConnectionHealthCalc(self.sql_driver)
                result += "Состояние подключений: " + await connection_health.connection_health_check() + "\n"

            if HealthType.VACUUM in health_types:
                vacuum_health = VacuumHealthCalc(self.sql_driver)
                result += "Состояние вакуума: " + await vacuum_health.transaction_id_danger_check() + "\n"

            if HealthType.SEQUENCE in health_types:
                sequence_health = SequenceHealthCalc(self.sql_driver)
                result += "Состояние последовательностей: " + await sequence_health.sequence_danger_check() + "\n"

            if HealthType.REPLICATION in health_types:
                replication_health = ReplicationCalc(self.sql_driver)
                result += "Состояние репликации: " + await replication_health.replication_health_check() + "\n"

            if HealthType.BUFFER in health_types:
                buffer_health = BufferHealthCalc(self.sql_driver)
                result += "Состояние буфера для индексов: " + await buffer_health.index_hit_rate() + "\n"
                result += "Состояние буфера для таблиц: " + await buffer_health.table_hit_rate() + "\n"

            if HealthType.CONSTRAINT in health_types:
                constraint_health = ConstraintHealthCalc(self.sql_driver)
                result += "Состояние ограничений: " + await constraint_health.invalid_constraints_check() + "\n"

            return result if result else "Проверки состояния не выполнялись."
        except Exception as e:
            logger.error(f"Ошибка при вычислении состояния базы данных: {e}", exc_info=True)
            return f"Ошибка при вычислении состояния базы данных: {e}"
