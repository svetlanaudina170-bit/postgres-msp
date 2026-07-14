# Анализ файла extension_utils.py
#
# Описание файла:
# Файл extension_utils.py содержит утилиты для работы с расширениями PostgreSQL.
# Он предоставляет функции для проверки версии PostgreSQL, статуса установленных или доступных расширений,
# а также для проверки соответствия минимальным требованиям версии для определенных функций.
# Основное назначение — упрощение управления расширениями, такими как pg_stat_statements и hypopg.
#
# Используемые модули:
# - logging: для логирования событий
# - dataclasses: для создания класса ExtensionStatus с минимальным boilerplate-кодом
# - typing: для аннотаций типов (Literal)
#
# Импорты:
# - SafeSqlDriver: класс для безопасного выполнения SQL запросов
# - SqlDriver: базовый класс для выполнения SQL запросов
#
# Основные компоненты:
# - Переменная _POSTGRES_VERSION: глобальный кэш версии PostgreSQL
# - Класс ExtensionStatus: структура данных для представления статуса расширения
# - Функции: reset_postgres_version_cache, get_postgres_version, check_postgres_version_requirement,
#   check_extension, check_hypopg_installation_status
#
# Зависимости:
# Файл связан с модулями safe_sql и sql_driver, используется в top_queries_calc.py и других частях пакета,
# взаимодействующих с PostgreSQL.

"""Утилиты для работы с расширениями PostgreSQL."""

import logging
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

from .safe_sql import SafeSqlDriver
from .sql_driver import SqlDriver

# Инициализация логгера
logger = logging.getLogger(__name__)

# Глобальный кэш версии PostgreSQL
# TODO: В будущем, при поддержке множественных подключений, кэш должен быть специфичным для каждого подключения
_POSTGRES_VERSION: Optional[int] = None


# Описание класса ExtensionStatus
#
# Класс ExtensionStatus представляет статус расширения PostgreSQL, включая информацию
# об установке, доступности и версии.
@dataclass
class ExtensionStatus:
    """Статус расширения PostgreSQL."""

    is_installed: bool  # Установлено ли расширение
    is_available: bool  # Доступно ли расширение для установки
    name: str  # Имя расширения
    message: str  # Сообщение о статусе
    default_version: Optional[str]  # Версия по умолчанию, если доступна


def reset_postgres_version_cache() -> None:
    """
    Описание функции reset_postgres_version_cache:
    Сбрасывает глобальный кэш версии PostgreSQL. Используется преимущественно для тестирования.

    Возвращает:
        None
    """
    global _POSTGRES_VERSION
    _POSTGRES_VERSION = None


async def get_postgres_version(sql_driver: SqlDriver) -> int:
    """
    Описание функции get_postgres_version:
    Получает основную версию PostgreSQL в виде целого числа.

    Аргументы:
        sql_driver (SqlDriver): Экземпляр SqlDriver для выполнения запросов

    Возвращает:
        int: Основная версия PostgreSQL (например, 16 для PostgreSQL 16.2) или 0, если версию определить не удалось

    Исключения:
        ValueError: Если произошла ошибка при определении версии
    """
    global _POSTGRES_VERSION
    if _POSTGRES_VERSION is not None:
        return _POSTGRES_VERSION

    try:
        # Выполнение запроса для получения версии сервера
        rows = await sql_driver.execute_query("SHOW server_version;")
        if not rows:
            logger.warning("Не удалось определить версию PostgreSQL")
            return 0

        # Извлечение строки версии
        version_string: str = rows[0].cells.get("server_version")
        # Извлечение основной версии (до первой точки)
        major_version: str = version_string.split(".")[0]
        # Преобразование в целое число
        version: int = int(major_version)

        # Кэширование результата
        _POSTGRES_VERSION = version
        logger.debug(f"Версия PostgreSQL определена: {version}")

        return version
    except Exception as e:
        logger.error(f"Ошибка при определении версии PostgreSQL: {e}", exc_info=True)
        raise ValueError("Ошибка при определении версии PostgreSQL") from e


async def check_postgres_version_requirement(sql_driver: SqlDriver, min_version: int, feature_name: str) -> Tuple[bool, str]:
    """
    Описание функции check_postgres_version_requirement:
    Проверяет, соответствует ли версия PostgreSQL минимальным требованиям для указанной функции.

    Аргументы:
        sql_driver (SqlDriver): Экземпляр SqlDriver для выполнения запросов
        min_version (int): Минимально требуемая версия PostgreSQL
        feature_name (str): Название функции, требующей эту версию

    Возвращает:
        Tuple[bool, str]: Кортеж из флага соответствия требованиям и сообщения
    """
    # Получение версии PostgreSQL
    pg_version: int = await get_postgres_version(sql_driver)

    # Проверка соответствия версии
    if pg_version >= min_version:
        return True, f"Версия PostgreSQL {pg_version} соответствует требованиям для {feature_name}"

    return False, (
        f"Для функции {feature_name} требуется PostgreSQL {min_version} или новее. Текущая версия: PostgreSQL {pg_version or 'неизвестна'}."
    )


async def check_extension(
    sql_driver: SqlDriver, extension_name: str, include_messages: bool = True, message_type: Literal["plain", "markdown"] = "plain"
) -> ExtensionStatus:
    """
    Описание функции check_extension:
    Проверяет, установлено или доступно ли расширение PostgreSQL.

    Аргументы:
        sql_driver (SqlDriver): Экземпляр SqlDriver для выполнения запросов
        extension_name (str): Имя расширения для проверки
        include_messages (bool): Включать ли пользовательские сообщения в результат
        message_type (Literal["plain", "markdown"]): Формат сообщений — plain или markdown

    Возвращает:
        ExtensionStatus: Объект с полями:
            - is_installed: Установлено ли расширение
            - is_available: Доступно ли расширение
            - name: Имя расширения
            - message: Пользовательское сообщение о статусе
            - default_version: Версия по умолчанию, если доступна
    """
    # Проверка, установлено ли расширение
    installed_result = await SafeSqlDriver.execute_param_query(
        sql_driver,
        "SELECT extversion FROM pg_extension WHERE extname = {};",
        [extension_name],
    )

    # Инициализация результата
    result = ExtensionStatus(
        is_installed=False,
        is_available=False,
        name=extension_name,
        message="",
        default_version=None,
    )

    if installed_result and len(installed_result) > 0:
        # Расширение установлено
        version: str = installed_result[0].cells.get("extversion", "неизвестна")
        result.is_installed = True
        result.is_available = True
        if include_messages:
            if message_type == "markdown":
                result.message = f"Расширение **{extension_name}** (версия {version}) уже установлено."
            else:
                result.message = f"Расширение {extension_name} (версия {version}) уже установлено."
    else:
        # Проверка доступности расширения
        available_result = await SafeSqlDriver.execute_param_query(
            sql_driver,
            "SELECT default_version FROM pg_available_extensions WHERE name = {};",
            [extension_name],
        )

        if available_result and len(available_result) > 0:
            # Расширение доступно, но не установлено
            result.is_available = True
            result.default_version = available_result[0].cells.get("default_version")
            if include_messages:
                if message_type == "markdown":
                    result.message = (
                        f"Расширение **{extension_name}** доступно, но не установлено.\n\n"
                        f"Вы можете установить его, выполнив: `CREATE EXTENSION {extension_name};`."
                    )
                else:
                    result.message = (
                        f"Расширение {extension_name} доступно, но не установлено.\n"
                        f"Вы можете установить его, выполнив: CREATE EXTENSION {extension_name};"
                    )
        else:
            # Расширение недоступно
            if include_messages:
                if message_type == "markdown":
                    result.message = (
                        f"Расширение **{extension_name}** недоступно на этом сервере PostgreSQL.\n\n"
                        f"Для установки необходимо:\n"
                        f"1. Установить пакет расширения на сервере\n"
                        f"2. Выполнить: `CREATE EXTENSION {extension_name};`"
                    )
                else:
                    result.message = (
                        f"Расширение {extension_name} недоступно на этом сервере PostgreSQL.\n"
                        f"Для установки необходимо:\n"
                        f"1. Установить пакет расширения на сервере\n"
                        f"2. Выполнить: CREATE EXTENSION {extension_name};"
                    )

    return result


async def check_hypopg_installation_status(sql_driver: SqlDriver, message_type: Literal["plain", "markdown"] = "markdown") -> Tuple[bool, str]:
    """
    Описание функции check_hypopg_installation_status:
    Проверяет статус установки расширения HypoPG и возвращает подробное сообщение.

    Аргументы:
        sql_driver (SqlDriver): Экземпляр SqlDriver для выполнения запросов
        message_type (Literal["plain", "markdown"]): Формат сообщений — plain или markdown

    Возвращает:
        Tuple[bool, str]: Кортеж из флага установки и сообщения о статусе
    """
    # Проверка статуса расширения HypoPG
    status = await check_extension(sql_driver, "hypopg", include_messages=False)

    if status.is_installed:
        # Расширение установлено
        if message_type == "markdown":
            return True, "Расширение **hypopg** уже установлено."
        else:
            return True, "Расширение hypopg уже установлено."

    if status.is_available:
        # Расширение доступно, но не установлено
        if message_type == "markdown":
            return False, (
                "Расширение **hypopg** требуется для тестирования гипотетических индексов, но оно не установлено.\n\n"
                "Вы можете запросить установку 'hypopg' с помощью инструмента 'execute_query'.\n\n"
                "**Безопасно ли это?** Установка 'hypopg' обычно безопасна и является стандартной практикой "
                "для тестирования индексов. Оно создает виртуальный слой, имитирующий индексы без их реального создания. "
                "Для установки требуются привилегии базы данных (часто суперпользователь).\n\n"
                "**Что оно делает?** Позволяет создавать виртуальные индексы и тестировать их влияние на производительность "
                "без накладных расходов на реальное создание индексов.\n\n"
                "**Как отменить?** Если вы решите удалить его, вы можете запросить выполнение 'DROP EXTENSION hypopg;'."
            )
        else:
            return False, (
                "Расширение hypopg требуется для тестирования гипотетических индексов, но оно не установлено.\n"
                "Вы можете запросить его установку с помощью инструмента 'execute_query'.\n"
                "Оно безопасно и позволяет тестировать индексы без их создания."
            )

    # Расширение недоступно
    pg_version: int = await get_postgres_version(sql_driver)
    major_version_str: str = f"{pg_version}" if pg_version > 0 else "XX"
    if message_type == "markdown":
        return False, (
            "Расширение **hypopg** недоступно на этом сервере PostgreSQL.\n\n"
            "Для установки HypoPG:\n"
            f"1. Для Debian/Ubuntu: `sudo apt-get install postgresql-{major_version_str}-hypopg`\n"
            f"2. Для RHEL/CentOS: `sudo yum install postgresql{major_version_str}-hypopg`\n"
            f"3. Для MacOS с Homebrew: `brew install hypopg`\n"
            f"4. Для других систем, соберите из исходников: `git clone https://github.com/HypoPG/hypopg`\n\n"
            "После установки пакетов расширения подключитесь к базе данных и выполните: `CREATE EXTENSION hypopg;`"
        )
    else:
        return False, (
            "Расширение hypopg недоступно на этом сервере PostgreSQL.\n"
            "Для установки HypoPG:\n"
            f"1. Для Debian/Ubuntu: sudo apt-get install postgresql-{major_version_str}-hypopg\n"
            f"2. Для RHEL/CentOS: sudo yum install postgresql{major_version_str}-hypopg\n"
            f"3. Для MacOS с Homebrew: brew install hypopg\n"
            f"4. Для других систем, соберите из исходников: git clone https://github.com/HypoPG/hypopg\n"
            "После установки пакетов расширения подключитесь к базе данных и выполните: CREATE EXTENSION hypopg;"
        )
