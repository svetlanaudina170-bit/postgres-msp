# Анализ файла top_queries_calc.py
#
# Описание файла:
# Файл top_queries_calc.py содержит класс TopQueriesCalc, предназначенный для анализа и извлечения наиболее медленных
# или ресурсоемких SQL запросов из PostgreSQL с использованием расширения pg_stat_statements.
# Класс предоставляет два метода: для получения запросов, отсортированных по времени выполнения,
# и для получения запросов, потребляющих значительные ресурсы (время, буферы, WAL).
#
# Используемые модули:
# - logging: для логирования событий
# - typing: для аннотаций типов (Literal, LiteralString, Union, cast)
#
# Импорты:
# - SafeSqlDriver, SqlDriver: драйверы для выполнения SQL запросов
# - check_extension, get_postgres_version: утилиты для проверки расширений и версии PostgreSQL
#
# Основные компоненты:
# - Константа PG_STAT_STATEMENTS: имя расширения PostgreSQL для статистики запросов
# - Переменная install_pg_stat_statements_message: сообщение с инструкциями по установке pg_stat_statements
# - Класс TopQueriesCalc: инструмент для анализа медленных и ресурсоемких запросов
#
# Зависимости:
# Файл связан с модулями sql (SafeSqlDriver, SqlDriver) и server.py, где TopQueriesCalc используется
# для обработки запросов через инструмент get_top_queries.

import logging
from typing import Literal, LiteralString, Union, cast

from ..sql import SafeSqlDriver, SqlDriver
from ..sql.extension_utils import check_extension, get_postgres_version

# Инициализация логгера
logger = logging.getLogger(__name__)

# Константа для имени расширения PostgreSQL
PG_STAT_STATEMENTS: str = "pg_stat_statements"

# Сообщение с инструкциями по установке pg_stat_statements
install_pg_stat_statements_message: str = (
    "Для отчета о медленных запросах требуется расширение pg_stat_statements, "
    "но оно не установлено.\n\n"
    "Вы можете установить его, выполнив: "
    "`CREATE EXTENSION pg_stat_statements;`\n\n"
    "**Что оно делает?** Записывает статистику (время выполнения, количество вызовов, возвращенные строки) "
    "для каждого запроса, выполненного в базе данных.\n\n"
    "**Безопасно ли это?** Установка 'pg_stat_statements' обычно безопасна и является стандартной практикой "
    "для мониторинга производительности. Оно добавляет накладные расходы на отслеживание статистики, "
    "но это обычно незначительно, если только база не находится под экстремальной нагрузкой."
)


# Описание класса TopQueriesCalc
#
# Класс TopQueriesCalc предоставляет методы для извлечения медленных или ресурсоемких SQL запросов
# на основе данных из расширения pg_stat_statements.
class TopQueriesCalc:
    """Инструмент для извлечения самых медленных SQL запросов."""

    sql_driver: Union[SqlDriver, SafeSqlDriver]  # SQL драйвер для выполнения запросов

    def __init__(self, sql_driver: Union[SqlDriver, SafeSqlDriver]) -> None:
        """
        Описание метода __init__:
        Инициализирует объект TopQueriesCalc с указанным SQL драйвером.

        Аргументы:
            sql_driver (Union[SqlDriver, SafeSqlDriver]): SQL драйвер для взаимодействия с базой данных

        Возвращает:
            None
        """
        # Сохранение SQL драйвера
        self.sql_driver = sql_driver

    async def get_top_queries_by_time(self, limit: int = 10, sort_by: Literal["total", "mean"] = "mean") -> str:
        """
        Описание метода get_top_queries_by_time:
        Возвращает список самых медленных SQL запросов на основе времени выполнения.
        Запросы сортируются по общему времени выполнения (total) или среднему времени на вызов (mean).

        Аргументы:
            limit (int): Количество возвращаемых запросов (по умолчанию: 10)
            sort_by (Literal["total", "mean"]): Критерий сортировки — total для общего времени
                                               или mean для среднего времени на вызов (по умолчанию)

        Возвращает:
            str: Текстовое представление медленных запросов или инструкции по установке расширения
        """
        try:
            # Логирование параметров запроса
            logger.debug(f"Получение медленных запросов по времени. limit={limit}, sort_by={sort_by}")
            # Проверка установки расширения pg_stat_statements
            extension_status = await check_extension(
                self.sql_driver,
                PG_STAT_STATEMENTS,
                include_messages=False,
            )

            # Если расширение не установлено, возвращаем инструкции
            if not extension_status.is_installed:
                logger.warning(f"Расширение {PG_STAT_STATEMENTS} не установлено")
                return install_pg_stat_statements_message

            # Получение версии PostgreSQL
            pg_version: int = await get_postgres_version(self.sql_driver)
            logger.debug(f"Версия PostgreSQL: {pg_version}")

            # Определение имен столбцов в зависимости от версии PostgreSQL
            if pg_version >= 13:
                # PostgreSQL 13 и новее
                total_time_col: str = "total_exec_time"
                mean_time_col: str = "mean_exec_time"
            else:
                # PostgreSQL 12 и старее
                total_time_col: str = "total_time"
                mean_time_col: str = "mean_time"

            logger.debug(f"Используемые столбцы времени: total={total_time_col}, mean={mean_time_col}")

            # Определение столбца для сортировки
            order_by_column: str = total_time_col if sort_by == "total" else mean_time_col

            # Формирование SQL запроса
            query: LiteralString = f"""
                SELECT
                    query,
                    calls,
                    {total_time_col},
                    {mean_time_col},
                    rows
                FROM pg_stat_statements
                ORDER BY {order_by_column} DESC
                LIMIT {{}};
            """
            logger.debug(f"Выполнение запроса: {query}")
            # Выполнение параметризованного запроса
            slow_query_rows = await SafeSqlDriver.execute_param_query(
                self.sql_driver,
                query,
                [limit],
            )
            # Извлечение результатов
            slow_queries: list[dict] = [row.cells for row in slow_query_rows] if slow_query_rows else []
            logger.info(f"Найдено {len(slow_queries)} медленных запросов")

            # Формирование описания результата
            if sort_by == "total":
                criteria: str = "общему времени выполнения"
            else:
                criteria: str = "среднему времени на вызов"

            # Формирование итогового текста
            result: str = f"Топ {len(slow_queries)} самых медленных запросов по {criteria}:\n"
            result += str(slow_queries)
            return result
        except Exception as e:
            # Логирование ошибки
            logger.error(f"Ошибка при получении медленных запросов: {e}", exc_info=True)
            # Возврат сообщения об ошибке
            return f"Ошибка при получении медленных запросов: {e}"

    async def get_top_resource_queries(self, frac_threshold: float = 0.05) -> str:
        """
        Описание метода get_top_resource_queries:
        Возвращает список наиболее ресурсоемких запросов на основе комбинации метрик
        (время выполнения, доступ к буферам, WAL). Запросы фильтруются по порогу доли ресурсов.

        Аргументы:
            frac_threshold (float): Пороговое значение доли ресурсов для фильтрации (по умолчанию: 0.05)

        Возвращает:
            str: Текстовое представление ресурсоемких запросов или сообщение об ошибке
        """
        try:
            # Логирование параметров запроса
            logger.debug(f"Получение ресурсоемких запросов с порогом {frac_threshold}")
            # Проверка установки расширения pg_stat_statements
            extension_status = await check_extension(
                self.sql_driver,
                PG_STAT_STATEMENTS,
                include_messages=False,
            )

            # Если расширение не установлено, возвращаем инструкции
            if not extension_status.is_installed:
                logger.warning(f"Расширение {PG_STAT_STATEMENTS} не установлено")
                return install_pg_stat_statements_message

            # Получение версии PostgreSQL
            pg_version: int = await get_postgres_version(self.sql_driver)
            logger.debug(f"Версия PostgreSQL: {pg_version}")

            # Определение имен столбцов в зависимости от версии PostgreSQL
            if pg_version >= 13:
                # PostgreSQL 13 и новее
                total_time_col: str = "total_exec_time"
                mean_time_col: str = "mean_exec_time"
            else:
                # PostgreSQL 12 и старее
                total_time_col: str = "total_time"
                mean_time_col: str = "mean_time"

            # Формирование SQL запроса
            query: LiteralString = cast(
                LiteralString,
                f"""
                WITH resource_fractions AS (
                    SELECT
                        query,
                        calls,
                        rows,
                        {total_time_col} total_exec_time,
                        {mean_time_col} mean_exec_time,
                        stddev_exec_time,
                        shared_blks_hit,
                        shared_blks_read,
                        shared_blks_dirtied,
                        wal_bytes,
                        total_exec_time / SUM(total_exec_time) OVER () AS total_exec_time_frac,
                        (shared_blks_hit + shared_blks_read) / SUM(shared_blks_hit + shared_blks_read) OVER () AS shared_blks_accessed_frac,
                        shared_blks_read / SUM(shared_blks_read) OVER () AS shared_blks_read_frac,
                        shared_blks_dirtied / SUM(shared_blks_dirtied) OVER () AS shared_blks_dirtied_frac,
                        wal_bytes / SUM(wal_bytes) OVER () AS total_wal_bytes_frac
                    FROM pg_stat_statements
                )
                SELECT
                    query,
                    calls,
                    rows,
                    total_exec_time,
                    mean_exec_time,
                    stddev_exec_time,
                    total_exec_time_frac,
                    shared_blks_accessed_frac,
                    shared_blks_read_frac,
                    shared_blks_dirtied_frac,
                    total_wal_bytes_frac,
                    shared_blks_hit,
                    shared_blks_read,
                    shared_blks_dirtied,
                    wal_bytes
                FROM resource_fractions
                WHERE
                    total_exec_time_frac > {frac_threshold}
                    OR shared_blks_accessed_frac > {frac_threshold}
                    OR shared_blks_read_frac > {frac_threshold}
                    OR shared_blks_dirtied_frac > {frac_threshold}
                    OR total_wal_bytes_frac > {frac_threshold}
                ORDER BY total_exec_time DESC
                """,
            )

            # Логирование SQL запроса
            logger.debug(f"Выполнение запроса: {query}")
            # Выполнение параметризованного запроса
            slow_query_rows = await SafeSqlDriver.execute_param_query(
                self.sql_driver,
                query,
            )
            # Извлечение результатов
            resource_queries: list[dict] = [row.cells for row in slow_query_rows] if slow_query_rows else []
            # Логирование количества найденных запросов
            logger.info(f"Найдено {len(resource_queries)} ресурсоемких запросов")

            # Возврат результатов в текстовом виде
            return str(resource_queries)
        except Exception as e:
            # Логирование ошибки
            logger.error(f"Ошибка при получении ресурсоемких запросов: {e}", exc_info=True)
            # Возврат сообщения об ошибке
            return f"Ошибка при получении ресурсоемких запросов: {e}"
