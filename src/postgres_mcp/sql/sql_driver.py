# Анализ файла sql_driver.py
#
# Описание файла:
# Файл sql_driver.py содержит классы и функции для управления подключениями к PostgreSQL и выполнения SQL-запросов.
# Он предоставляет адаптер SqlDriver для взаимодействия с базой данных через библиотеку psycopg, а также
# класс DbConnPool для управления пулом асинхронных подключений. Функция obfuscate_password используется
# для маскировки паролей в строках подключения и сообщениях об ошибках.
#
# Используемые модули:
# - logging: для логирования событий
# - re: для работы с регулярными выражениями
# - dataclasses: для создания класса RowResult
# - typing: для аннотаций типов
# - urllib.parse: для парсинга и формирования URL подключения
# - psycopg.rows: для настройки формата возвращаемых строк
# - psycopg_pool: для создания пула асинхронных подключений
# - typing_extensions: для поддержки LiteralString
#
# Основные компоненты:
# - Функция obfuscate_password: маскирует пароли в строках подключения
# - Класс DbConnPool: управляет пулом асинхронных подключений
# - Класс SqlDriver: адаптер для выполнения SQL-запросов
#
# Зависимости:
# Файл используется в других модулях пакета, таких как safe_sql.py, bind_params.py и extension_utils.py,
# для выполнения SQL-запросов к PostgreSQL.

"""Адаптер SQL-драйвера для подключений к PostgreSQL."""

import logging
import re
from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from urllib.parse import urlparse
from urllib.parse import urlunparse

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from typing_extensions import LiteralString

# Инициализация логгера
logger = logging.getLogger(__name__)


def obfuscate_password(text: Optional[str]) -> Optional[str]:
    """
    Описание функции obfuscate_password:
    Маскирует пароль в тексте, содержащем информацию о подключении.
    Работает с URL подключения, сообщениями об ошибках и другими строками.

    Аргументы:
        text (Optional[str]): Текст, содержащий информацию о подключении

    Возвращает:
        Optional[str]: Текст с замаскированным паролем или None, если входной текст None
    """
    if text is None:
        return None
    if not text:
        return text

    # Попытка обработки как URL
    try:
        parsed = urlparse(text)
        if parsed.scheme and parsed.netloc and parsed.password:
            # Замена пароля на звездочки
            netloc: str = parsed.netloc.replace(parsed.password, "****")
            return urlunparse(parsed._replace(netloc=netloc))
    except Exception:
        pass

    # Обработка строк подключения, не являющихся корректными URL
    url_pattern = re.compile(r"(postgres(?:ql)?:\/\/[^:]+:)([^@]+)(@[^\/\s]+)")
    text = re.sub(url_pattern, r"\1****\3", text)

    # Обработка параметра password в строках подключения
    param_pattern = re.compile(r'(password=)([^\s&;"\']+)', re.IGNORECASE)
    text = re.sub(param_pattern, r"\1****", text)

    # Обработка DSN-формата с одинарными кавычками
    dsn_single_quote = re.compile(r"(password\s*=\s*')([^']+)(')", re.IGNORECASE)
    text = re.sub(dsn_single_quote, r"\1****\3", text)

    # Обработка DSN-формата с двойными кавычками
    dsn_double_quote = re.compile(r'(password\s*=\s*")([^"]+)(")', re.IGNORECASE)
    text = re.sub(dsn_double_quote, r"\1****\3", text)

    return text


# Описание класса DbConnPool
#
# Класс DbConnPool управляет пулом асинхронных подключений к PostgreSQL с помощью psycopg_pool.
# Поддерживает инициализацию, тестирование подключения и закрытие пула.
class DbConnPool:
    """Менеджер подключений к базе данных с использованием пула подключений psycopg."""

    def __init__(self, connection_url: Optional[str] = None) -> None:
        """
        Описание метода __init__:
        Инициализирует объект DbConnPool с опциональным URL подключения.

        Аргументы:
            connection_url (Optional[str]): URL подключения к базе данных

        Возвращает:
            None
        """
        self.connection_url: Optional[str] = connection_url
        self.pool: Optional[AsyncConnectionPool] = None
        self._is_valid: bool = False
        self._last_error: Optional[str] = None

    async def pool_connect(self, connection_url: Optional[str] = None) -> AsyncConnectionPool:
        """
        Описание метода pool_connect:
        Инициализирует пул подключений с логикой повторных попыток.

        Аргументы:
            connection_url (Optional[str]): URL подключения (если не указан, используется self.connection_url)

        Возвращает:
            AsyncConnectionPool: Инициализированный пул подключений

        Исключения:
            ValueError: Если URL подключения не предоставлен или подключение не удалось
        """
        if self.pool and self._is_valid:
            return self.pool

        url: Optional[str] = connection_url or self.connection_url
        self.connection_url = url
        if not url:
            self._is_valid = False
            self._last_error = "URL подключения к базе данных не предоставлен"
            raise ValueError(self._last_error)

        await self.close()

        try:
            self.pool = AsyncConnectionPool(
                conninfo=url,
                min_size=1,
                max_size=5,
                open=False,
            )
            await self.pool.open()

            async with self.pool.connection() as conn:
                async with conn.cursor() as cursor:
                    await cursor.execute("SELECT 1")

            self._is_valid = True
            self._last_error = None
            return self.pool
        except Exception as e:
            self._is_valid = False
            self._last_error = str(e)
            await self.close()
            raise ValueError(f"Не удалось установить соединение: {obfuscate_password(str(e))}") from e

    async def close(self) -> None:
        """
        Описание метода close:
        Закрывает пул подключений.

        Возвращает:
            None
        """
        if self.pool:
            try:
                await self.pool.close()
            except Exception as e:
                logger.warning(f"Ошибка при закрытии пула подключений: {e}")
            finally:
                self.pool = None
                self._is_valid = False

    @property
    def is_valid(self) -> bool:
        """
        Описание свойства is_valid:
        Проверяет, является ли пул подключений валидным.

        Возвращает:
            bool: True, если пул валиден
        """
        return self._is_valid

    @property
    def last_error(self) -> Optional[str]:
        """
        Описание свойства last_error:
        Возвращает последнее сообщение об ошибке.

        Возвращает:
            Optional[str]: Сообщение об ошибке или None
        """
        return self._last_error


# Описание класса SqlDriver
#
# Класс SqlDriver предоставляет интерфейс для выполнения SQL-запросов к PostgreSQL.
# Поддерживает как прямые подключения, так и пулы подключений через DbConnPool.
class SqlDriver:
    """Адаптер для подключения к PostgreSQL с интерфейсом, ожидаемым DTA."""

    @dataclass
    class RowResult:
        """Класс для представления результата строки в формате Griptape RowResult."""

        cells: Dict[str, Any]  # Словарь с данными строки

    def __init__(
        self,
        conn: Any = None,
        engine_url: Optional[str] = None,
    ) -> None:
        """
        Описание метода __init__:
        Инициализирует SqlDriver с подключением или URL базы данных.

        Аргументы:
            conn (Any): Объект подключения или пул подключений
            engine_url (Optional[str]): URL подключения к базе данных

        Возвращает:
            None

        Исключения:
            ValueError: Если не предоставлены ни conn, ни engine_url
        """
        if conn:
            self.conn: Any = conn
            self.is_pool: bool = isinstance(conn, DbConnPool)
        elif engine_url:
            self.engine_url: str = engine_url
            self.conn = None
            self.is_pool = False
        else:
            raise ValueError("Необходимо предоставить либо conn, либо engine_url")

    def connect(self) -> Any:
        """
        Описание метода connect:
        Устанавливает подключение, если оно еще не установлено.

        Возвращает:
            Any: Объект подключения или пул подключений

        Исключения:
            ValueError: Если подключение не удалось установить
        """
        if self.conn is not None:
            return self.conn
        if self.engine_url:
            self.conn = DbConnPool(self.engine_url)
            self.is_pool = True
            return self.conn
        raise ValueError("Подключение не установлено. Необходимо предоставить conn или engine_url")

    async def execute_query(
        self,
        query: LiteralString,
        params: Optional[List[Any]] = None,
        force_readonly: bool = False,
    ) -> Optional[List[RowResult]]:
        """
        Описание метода execute_query:
        Выполняет SQL-запрос и возвращает результаты.

        Аргументы:
            query (LiteralString): SQL-запрос для выполнения
            params (Optional[List[Any]]): Параметры запроса
            force_readonly (bool): Принудительный режим только для чтения

        Возвращает:
            Optional[List[RowResult]]: Список результатов или None при ошибке

        Исключения:
            Exception: При ошибке выполнения запроса
        """
        try:
            if self.conn is None:
                self.connect()
                if self.conn is None:
                    raise ValueError("Подключение не установлено")

            if self.is_pool:
                pool: AsyncConnectionPool = await self.conn.pool_connect()
                async with pool.connection() as connection:
                    return await self._execute_with_connection(connection, query, params, force_readonly)
            else:
                return await self._execute_with_connection(self.conn, query, params, force_readonly)
        except Exception as e:
            if self.conn and self.is_pool:
                self.conn._is_valid = False
                self.conn._last_error = str(e)
            elif self.conn and not self.is_pool:
                self.conn = None
            raise

    async def _execute_with_connection(
        self, connection: Any, query: LiteralString, params: Optional[List[Any]], force_readonly: bool
    ) -> Optional[List[RowResult]]:
        """
        Описание метода _execute_with_connection:
        Выполняет запрос с использованием предоставленного подключения.

        Аргументы:
            connection (Any): Объект подключения
            query (LiteralString): SQL-запрос
            params (Optional[List[Any]]): Параметры запроса
            force_readonly (bool): Принудительный режим только для чтения

        Возвращает:
            Optional[List[RowResult]]: Список результатов или None

        Исключения:
            Exception: При ошибке выполнения запроса
        """
        transaction_started: bool = False
        try:
            async with connection.cursor(row_factory=dict_row) as cursor:
                if force_readonly:
                    await cursor.execute("BEGIN TRANSACTION READ ONLY")
                    transaction_started = True

                if params:
                    await cursor.execute(query, params)
                else:
                    await cursor.execute(query)

                while cursor.nextset():
                    pass

                if cursor.description is None:
                    if not force_readonly:
                        await cursor.execute("COMMIT")
                    elif transaction_started:
                        await cursor.execute("ROLLBACK")
                        transaction_started = False
                    return None

                rows = await cursor.fetchall()

                if not force_readonly:
                    await cursor.execute("COMMIT")
                elif transaction_started:
                    await cursor.execute("ROLLBACK")
                    transaction_started = False

                return [SqlDriver.RowResult(cells=dict(row)) for row in rows]
        except Exception as e:
            if transaction_started:
                try:
                    await connection.rollback()
                except Exception as rollback_error:
                    logger.error(f"Ошибка при откате транзакции: {rollback_error}")
            logger.error(f"Ошибка при выполнении запроса ({query}): {e}")
            raise

    async def close(self) -> None:
        """
        Описание метода close:
        Закрывает подключение или пул подключений, если они инициализированы.

        Возвращает:
            None
        """
        if self.conn is not None and self.is_pool:
            await self.conn.close()
            self.conn = None
