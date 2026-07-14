# Анализ файла __init__.py
#
# Описание файла:
# Файл __init__.py определяет содержимое модуля sql, экспортируя утилиты для работы с SQL в PostgreSQL.
# Он предоставляет классы и функции для управления подключениями к базе данных, безопасного выполнения запросов,
# обработки параметров, работы с расширениями и индексами, а также маскировки паролей.
#
# Используемые модули:
# - Отсутствуют (только относительные импорты из подмодулей)
#
# Импорты:
# - bind_params: утилиты для обработки параметров SQL запросов (ColumnCollector, SqlBindParams, TableAliasVisitor)
# - extension_utils: функции для работы с расширениями и версиями PostgreSQL (check_extension, check_hypopg_installation_status, check_postgres_version_requirement, get_postgres_version, reset_postgres_version_cache)
# - index: класс для определения индексов (IndexDefinition)
# - safe_sql: класс для безопасного выполнения SQL запросов (SafeSqlDriver)
# - sql_driver: классы для управления подключениями и выполнения запросов (DbConnPool, SqlDriver, obfuscate_password)
#
# Основные компоненты:
# - Переменная __all__: список экспортируемых элементов модуля
#
# Зависимости:
# Файл связан с подмодулями bind_params, extension_utils, index, safe_sql и sql_driver.
# Используется в контексте пакета, взаимодействующего с PostgreSQL, в частности в server.py и top_queries_calc.py.

"""Утилиты для работы с SQL."""

from .bind_params import ColumnCollector  # Класс для сбора столбцов
from .bind_params import SqlBindParams  # Класс для обработки параметров SQL
from .bind_params import TableAliasVisitor  # Класс для обработки псевдонимов таблиц
from .extension_utils import check_extension  # Функция проверки установки расширения
from .extension_utils import check_hypopg_installation_status  # Функция проверки установки hypopg
from .extension_utils import check_postgres_version_requirement  # Функция проверки версии PostgreSQL
from .extension_utils import get_postgres_version  # Функция получения версии PostgreSQL
from .extension_utils import reset_postgres_version_cache  # Функция сброса кэша версии PostgreSQL
from .index import IndexDefinition  # Класс для определения индексов
from .safe_sql import SafeSqlDriver  # Класс для безопасного выполнения SQL
from .sql_driver import DbConnPool  # Класс для пула соединений с базой данных
from .sql_driver import SqlDriver  # Класс для выполнения SQL запросов
from .sql_driver import obfuscate_password  # Функция для маскировки паролей

# Определение экспортируемых элементов модуля
__all__: list[str] = [
    "ColumnCollector",  # Класс для сбора столбцов
    "DbConnPool",  # Класс для пула соединений
    "IndexDefinition",  # Класс для определения индексов
    "SafeSqlDriver",  # Класс для безопасного выполнения SQL
    "SqlBindParams",  # Класс для параметров SQL
    "SqlDriver",  # Класс для выполнения SQL
    "TableAliasVisitor",  # Класс для псевдонимов таблиц
    "check_extension",  # Функция проверки расширения
    "check_hypopg_installation_status",  # Функция проверки hypopg
    "check_postgres_version_requirement",  # Функция проверки версии PostgreSQL
    "get_postgres_version",  # Функция получения версии PostgreSQL
    "obfuscate_password",  # Функция маскировки паролей
    "reset_postgres_version_cache",  # Функция сброса кэша версии
]
