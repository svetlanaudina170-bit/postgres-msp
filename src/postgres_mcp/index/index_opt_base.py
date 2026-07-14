# Анализ файла index_opt_base.py
#
# Описание файла:
# Файл index_opt_base.py содержит базовые классы и функции для оптимизации индексов в PostgreSQL.
# Он предоставляет абстрактный базовый класс IndexTuningBase, который реализует общую логику анализа
# рабочей нагрузки запросов и генерации рекомендаций по индексам. Также включает классы для представления
# рекомендаций по индексам (IndexRecommendation, IndexRecommendationAnalysis) и результатов анализа
# (IndexTuningResult). Функции, такие как candidate_str и pp_list, используются для форматирования данных.
#
# Используемые модули:
# - json: для обработки JSON-данных
# - logging: для логирования событий
# - time: для отслеживания времени выполнения
# - abc: для создания абстрактных базовых классов
# - dataclasses: для создания классов данных
# - typing: для аннотаций типов
# - pglast: для парсинга SQL-запросов
#
# Импорты из пакета:
# - calculate_improvement_multiple: для вычисления улучшения производительности
# - ExplainPlanTool: для генерации планов выполнения
# - IndexDefinition, SafeSqlDriver, SqlBindParams, SqlDriver, TableAliasVisitor, check_hypopg_installation_status:
#   для работы с SQL и базой данных
#
# Основные компоненты:
# - Класс IndexRecommendation: представляет рекомендацию по индексу
# - Класс IndexRecommendationAnalysis: содержит анализ рекомендации
# - Класс IndexTuningResult: хранит результаты анализа
# - Класс IndexTuningBase: базовый класс для оптимизации индексов
# - Функции pp_list и candidate_str: для форматирования данных
#
# Зависимости:
# Файл используется в других модулях, таких как dta_calc.py, и зависит от модулей sql (sql_driver.py, safe_sql.py)
# и других частей пакета для работы с PostgreSQL.

import json
import logging
import time
from abc import ABC
from abc import abstractmethod
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Dict
from typing import FrozenSet
from typing import Iterable
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple

from pglast import parse_sql
from pglast.ast import SelectStmt

from ..artifacts import calculate_improvement_multiple
from ..explain import ExplainPlanTool
from ..sql import IndexDefinition
from ..sql import SafeSqlDriver
from ..sql import SqlBindParams
from ..sql import SqlDriver
from ..sql import TableAliasVisitor
from ..sql import check_hypopg_installation_status

# Инициализация логгера
logger = logging.getLogger(__name__)

# Константа для ограничения количества анализируемых запросов
MAX_NUM_INDEX_TUNING_QUERIES: int = 10


def pp_list(lst: List[Any]) -> str:
    """
    Описание функции pp_list:
    Форматирует список для отладочного вывода.

    Аргументы:
        lst (List[Any]): Список элементов для форматирования

    Возвращает:
        str: Отформатированная строка
    """
    return ("\n  - " if len(lst) > 0 else "") + "\n  - ".join([str(item) for item in lst])


# Описание класса IndexRecommendation
#
# Класс IndexRecommendation представляет рекомендацию по индексу с оценкой размера и определением.
@dataclass
class IndexRecommendation:
    """Представляет индекс базы данных с оценкой размера и определением."""

    _definition: IndexDefinition
    estimated_size_bytes: int = 0
    potential_problematic_reason: Optional[str] = None

    def __init__(
        self,
        table: str,
        columns: Tuple[str, ...],
        using: str = "btree",
        estimated_size_bytes: int = 0,
        potential_problematic_reason: Optional[str] = None,
    ) -> None:
        """
        Описание метода __init__:
        Инициализирует IndexRecommendation с параметрами индекса.

        Аргументы:
            table (str): Имя таблицы
            columns (Tuple[str, ...]): Кортеж имен столбцов
            using (str): Тип индекса (по умолчанию "btree")
            estimated_size_bytes (int): Оценочный размер в байтах
            potential_problematic_reason (Optional[str]): Причина потенциальной проблемы

        Возвращает:
            None
        """
        self._definition = IndexDefinition(table, columns, using)
        self.estimated_size_bytes = estimated_size_bytes
        self.potential_problematic_reason = potential_problematic_reason

    @property
    def index_definition(self) -> IndexDefinition:
        """Возвращает объект IndexDefinition."""
        return self._definition

    @property
    def definition(self) -> str:
        """Возвращает строковое определение индекса."""
        return self._definition.definition

    @property
    def name(self) -> str:
        """Возвращает имя индекса."""
        return self._definition.name

    @property
    def columns(self) -> Tuple[str, ...]:
        """Возвращает кортеж столбцов индекса."""
        return self._definition.columns

    @property
    def table(self) -> str:
        """Возвращает имя таблицы индекса."""
        return self._definition.table

    @property
    def using(self) -> str:
        """Возвращает тип индекса."""
        return self._definition.using

    def __hash__(self) -> int:
        """Возвращает хэш объекта на основе определения индекса."""
        return self._definition.__hash__()

    def __eq__(self, other: Any) -> bool:
        """Сравнивает два объекта IndexRecommendation."""
        return self._definition.__eq__(other.index_definition) if hasattr(other, "index_definition") else False

    def __str__(self) -> str:
        """Возвращает строковое представление объекта."""
        return f"{self._definition} (estimated_size_bytes: {self.estimated_size_bytes})"

    def __repr__(self) -> str:
        """Возвращает детальное строковое представление объекта."""
        return f"{self._definition!r} (estimated_size_bytes: {self.estimated_size_bytes})"


# Описание класса IndexRecommendationAnalysis
#
# Класс IndexRecommendationAnalysis содержит анализ рекомендации по индексу с оценкой выгоды.
@dataclass
class IndexRecommendationAnalysis:
    """Представляет рекомендованный индекс с оценкой выгоды."""

    index_recommendation: IndexRecommendation
    progressive_base_cost: float
    progressive_recommendation_cost: float
    individual_base_cost: float
    individual_recommendation_cost: float
    queries: List[str]
    definition: str

    @property
    def table(self) -> str:
        """Возвращает имя таблицы индекса."""
        return self.index_recommendation.table

    @property
    def columns(self) -> Tuple[str, ...]:
        """Возвращает кортеж столбцов индекса."""
        return self.index_recommendation.columns

    @property
    def using(self) -> str:
        """Возвращает тип индекса."""
        return self.index_recommendation.using

    @property
    def progressive_improvement_multiple(self) -> float:
        """
        Описание свойства progressive_improvement_multiple:
        Вычисляет прогрессивное улучшение производительности от рекомендации.

        Возвращает:
            float: Множитель улучшения
        """
        return calculate_improvement_multiple(self.progressive_base_cost, self.progressive_recommendation_cost)

    @property
    def potential_problematic_reason(self) -> Optional[str]:
        """Возвращает причину потенциальной проблемы."""
        return self.index_recommendation.potential_problematic_reason

    @property
    def estimated_size_bytes(self) -> int:
        """Возвращает оценочный размер индекса в байтах."""
        return self.index_recommendation.estimated_size_bytes

    @property
    def individual_improvement_multiple(self) -> float:
        """
        Описание свойства individual_improvement_multiple:
        Вычисляет индивидуальное улучшение производительности от рекомендации.

        Возвращает:
            float: Множитель улучшения
        """
        return calculate_improvement_multiple(self.individual_base_cost, self.individual_recommendation_cost)

    def to_index(self) -> IndexRecommendation:
        """Возвращает объект IndexRecommendation."""
        return self.index_recommendation


# Описание класса IndexTuningResult
#
# Класс IndexTuningResult хранит результаты анализа оптимизации индексов.
@dataclass
class IndexTuningResult:
    """Результаты анализа оптимизации индексов."""

    session_id: str
    budget_mb: int
    workload_source: str = "n/a"
    workload: Optional[List[Dict[str, Any]]] = None
    recommendations: List[IndexRecommendationAnalysis] = field(default_factory=list)
    error: Optional[str] = None
    dta_traces: List[str] = field(default_factory=list)


def candidate_str(indexes: Iterable[IndexDefinition] | Iterable[IndexRecommendation] | Iterable[IndexRecommendationAnalysis]) -> str:
    """
    Описание функции candidate_str:
    Форматирует список индексов в строку для отображения.

    Аргументы:
        indexes (Iterable): Итератор объектов IndexDefinition, IndexRecommendation или IndexRecommendationAnalysis

    Возвращает:
        str: Отформатированная строка
    """
    return ", ".join(f"{idx.table}({','.join(idx.columns)})" for idx in indexes) if indexes else "(no indexes)"


# Описание класса IndexTuningBase
#
# Класс IndexTuningBase является абстрактным базовым классом для оптимизации индексов,
# предоставляя общую логику анализа рабочей нагрузки и генерации рекомендаций.
class IndexTuningBase(ABC):
    def __init__(self, sql_driver: SqlDriver) -> None:
        """
        Описание метода __init__:
        Инициализирует IndexTuningBase с драйвером SQL.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для доступа к базе данных

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver
        self.cost_cache: Dict[FrozenSet[IndexDefinition], float] = {}
        self._size_estimate_cache: Dict[Tuple[str, FrozenSet[str]], int] = {}
        self._table_size_cache: Dict[str, int] = {}
        self._estimate_table_size_cache: Dict[str, int] = {}
        self._explain_plans_cache: Dict[Tuple[str, FrozenSet[IndexDefinition]], Dict[str, Any]] = {}
        self._sql_bind_params = SqlBindParams(self.sql_driver)
        self._dta_traces: List[str] = []
        self.budget_mb: int = -1
        self._analysis_start_time: float = 0.0

    async def analyze_workload(
        self,
        workload: Optional[List[Dict[str, Any]]] = None,
        sql_file: Optional[str] = None,
        query_list: Optional[List[str]] = None,
        min_calls: int = 50,
        min_avg_time_ms: float = 5.0,
        limit: int = MAX_NUM_INDEX_TUNING_QUERIES,
        max_index_size_mb: int = -1,
    ) -> IndexTuningResult:
        """
        Описание метода analyze_workload:
        Анализирует рабочую нагрузку запросов и рекомендует индексы.

        Аргументы:
            workload (Optional[List[Dict[str, Any]]]): Явная рабочая нагрузка
            sql_file (Optional[str]): Путь к файлу SQL-запросов
            query_list (Optional[List[str]]): Список SQL-запросов
            min_calls (int): Минимальное количество вызовов для pg_stat_statements
            min_avg_time_ms (float): Минимальное среднее время выполнения в мс
            limit (int): Максимальное количество анализируемых запросов
            max_index_size_mb (int): Максимальный размер индексов в МБ

        Возвращает:
            IndexTuningResult: Результаты анализа
        """
        session_id: str = str(int(time.time()))
        self._analysis_start_time = time.time()
        self._dta_traces = []

        self._size_estimate_cache = {}
        if max_index_size_mb > 0:
            self.budget_mb = max_index_size_mb

        session = IndexTuningResult(session_id=session_id, budget_mb=max_index_size_mb)

        try:
            precheck_result = await self._run_prechecks(session)
            if precheck_result:
                return precheck_result

            if workload:
                logger.debug(f"Использование явной рабочей нагрузки с {len(workload)} запросами")
                session.workload_source = "args"
                session.workload = workload
            elif query_list:
                logger.debug(f"Использование предоставленного списка с {len(query_list)} запросами")
                session.workload_source = "query_list"
                session.workload = [{"query": query, "queryid": f"direct-{i}"} for i, query in enumerate(query_list)]
            elif sql_file:
                logger.debug(f"Чтение запросов из файла: {sql_file}")
                session.workload_source = "sql_file"
                session.workload = self._get_workload_from_file(sql_file)
            else:
                logger.debug("Использование статистики запросов из базы данных")
                session.workload_source = "query_store"
                session.workload = await self._get_query_stats(min_calls, min_avg_time_ms, limit)

            if not session.workload:
                logger.warning("Рабочая нагрузка для анализа отсутствует")
                return session

            session.workload = await self._validate_and_parse_workload(session.workload)
            query_weights: List[Tuple[str, SelectStmt, float]] = self._covert_workload_to_query_weights(session.workload)

            if not query_weights:
                self.dta_trace("Запросы не предоставлены")
                session.recommendations = []
            else:
                workload_queries: List[str] = [q for q, _, _ in query_weights]
                self.dta_trace(f"Запросы рабочей нагрузки ({len(workload_queries)}): {pp_list(workload_queries)}")

                recommendations: Tuple[Set[IndexRecommendation], float] = await self._generate_recommendations(query_weights)
                session.recommendations = await self._format_recommendations(query_weights, recommendations)

                await self.sql_driver.execute_query("SELECT hypopg_reset();")

        except Exception as e:
            logger.error(f"Ошибка при анализе рабочей нагрузки: {e}", exc_info=True)
            session.error = f"Ошибка при анализе рабочей нагрузки: {e}"

        session.dta_traces = self._dta_traces
        return session

    async def _run_prechecks(self, session: IndexTuningResult) -> Optional[IndexTuningResult]:
        """
        Описание метода _run_prechecks:
        Выполняет предварительные проверки перед анализом.

        Аргументы:
            session (IndexTuningResult): Текущий объект сессии

        Возвращает:
            Optional[IndexTuningResult]: Сессия с ошибкой, если проверки не пройдены
        """
        is_hypopg_installed, hypopg_message = await check_hypopg_installation_status(self.sql_driver)
        if not is_hypopg_installed:
            session.error = hypopg_message
            return session

        result = await self.sql_driver.execute_query("SELECT s.last_analyze FROM pg_stat_user_tables s ORDER BY s.last_analyze LIMIT 1;")
        if not result or not any(row.cells.get("last_analyze") is not None for row in result):
            error_message: str = (
                "Статистика устарела. Сначала необходимо выполнить анализ базы данных. "
                "Пожалуйста, выполните 'ANALYZE;' перед использованием советника по настройке. "
                "Без актуальной статистики рекомендации по индексам могут быть неточными."
            )
            session.error = error_message
            logger.error(error_message)
            return session

        return None

    async def _validate_and_parse_workload(self, workload: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Описание метода _validate_and_parse_workload:
        Проверяет и парсит рабочую нагрузку для анализа.

        Аргументы:
            workload (List[Dict[str, Any]]): Список данных рабочей нагрузки

        Возвращает:
            List[Dict[str, Any]]: Проверенная и распарсенная рабочая нагрузка
        """
        validated_workload: List[Dict[str, Any]] = []
        for q in workload:
            query_text: str = q["query"]
            if not query_text:
                logger.debug("Пропуск пустого запроса")
                continue
            query_text = query_text.strip().lower()
            query_text = await self._sql_bind_params.replace_parameters(query_text)

            parsed = parse_sql(query_text)
            if not parsed:
                logger.debug(f"Пропуск непарсируемого запроса: {query_text[:50]}...")
                continue
            stmt = parsed[0].stmt
            if not self._is_analyzable_stmt(stmt):
                logger.debug(f"Пропуск неанализируемого запроса: {query_text[:50]}...")
                continue

            q["query"] = query_text
            q["stmt"] = stmt
            validated_workload.append(q)
        return validated_workload

    def _covert_workload_to_query_weights(self, workload: List[Dict[str, Any]]) -> List[Tuple[str, SelectStmt, float]]:
        """
        Описание метода _covert_workload_to_query_weights:
        Преобразует рабочую нагрузку в веса запросов на основе частоты.

        Аргументы:
            workload (List[Dict[str, Any]]): Список данных рабочей нагрузки

        Возвращает:
            List[Tuple[str, SelectStmt, float]]: Список кортежей (запрос, AST, вес)
        """
        return [(q["query"], q["stmt"], self.convert_query_info_to_weight(q)) for q in workload]

    def convert_query_info_to_weight(self, query_info: Dict[str, Any]) -> float:
        """
        Описание метода convert_query_info_to_weight:
        Преобразует информацию о запросе в вес на основе частоты.

        Аргументы:
            query_info (Dict[str, Any]): Информация о запросе

        Возвращает:
            float: Вес запроса
        """
        return query_info.get("calls", 1.0) * query_info.get("avg_exec_time", 1.0)

    async def get_explain_plan_with_indexes(self, query_text: str, indexes: FrozenSet[IndexDefinition]) -> Dict[str, Any]:
        """
        Описание метода get_explain_plan_with_indexes:
        Получает план выполнения для запроса с указанными индексами, используя кэширование.

        Аргументы:
            query_text (str): Текст SQL-запроса
            indexes (FrozenSet[IndexDefinition]): Множество индексов

        Возвращает:
            Dict[str, Any]: План выполнения в виде словаря
        """
        cache_key: Tuple[str, FrozenSet[IndexDefinition]] = (query_text, indexes)
        existing_plan = self._explain_plans_cache.get(cache_key)
        if existing_plan:
            return existing_plan

        explain_plan_tool = ExplainPlanTool(self.sql_driver)
        plan: Dict[str, Any] = await explain_plan_tool.generate_explain_plan_with_hypothetical_indexes(query_text, indexes, False, self)
        self._explain_plans_cache[cache_key] = plan
        return plan

    def _get_workload_from_file(self, file_path: str) -> List[Dict[str, Any]]:
        """
        Описание метода _get_workload_from_file:
        Загружает запросы из SQL-файла.

        Аргументы:
            file_path (str): Путь к файлу

        Возвращает:
            List[Dict[str, Any]]: Список запросов

        Исключения:
            ValueError: При ошибке загрузки файла
        """
        try:
            with open(file_path) as f:
                content: str = f.read()
            query_texts: List[str] = [q.strip() for q in content.split(";") if q.strip()]
            queries: List[Dict[str, Any]] = [{"queryid": i, "query": text} for i, text in enumerate(query_texts)]
            return queries
        except Exception as e:
            raise ValueError(f"Ошибка при загрузке запросов из файла {file_path}") from e

    async def _get_query_stats(self, min_calls: int, min_avg_time_ms: float, limit: int) -> List[Dict[str, Any]]:
        """
        Описание метода _get_query_stats:
        Получает статистику запросов из pg_stat_statements.

        Аргументы:
            min_calls (int): Минимальное количество вызовов
            min_avg_time_ms (float): Минимальное среднее время выполнения
            limit (int): Ограничение количества запросов

        Возвращает:
            List[Dict[str, Any]]: Список статистики запросов
        """
        return await self._get_query_stats_direct(min_calls, min_avg_time_ms, limit)

    async def _get_query_stats_direct(self, min_calls: int = 50, min_avg_time_ms: float = 5.0, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Описание метода _get_query_stats_direct:
        Непосредственно собирает статистику запросов из pg_stat_statements.

        Аргументы:
            min_calls (int): Минимальное количество вызовов
            min_avg_time_ms (float): Минимальное среднее время выполнения
            limit (int): Ограничение количества запросов

        Возвращает:
            List[Dict[str, Any]]: Список статистики запросов
        """
        query: str = """
        SELECT queryid, query, calls, total_exec_time/calls as avg_exec_time
        FROM pg_stat_statements
        WHERE calls >= {}
        AND total_exec_time/calls >= {}
        ORDER BY total_exec_time DESC
        LIMIT {}
        """
        result = await SafeSqlDriver.execute_param_query(self.sql_driver, query, [min_calls, min_avg_time_ms, limit])
        return [dict(row.cells) for row in result] if result else []

    def _is_analyzable_stmt(self, stmt: Any) -> bool:
        """
        Описание метода _is_analyzable_stmt:
        Проверяет, можно ли анализировать запрос для рекомендаций индексов.

        Аргументы:
            stmt (Any): AST-узел запроса

        Возвращает:
            bool: True, если запрос анализируем
        """
        if not isinstance(stmt, SelectStmt):
            return False
        visitor = TableAliasVisitor()
        visitor(stmt)
        return not all(table.startswith("pg_") or table.startswith("aurora_") for table in visitor.tables)

    def dta_trace(self, message: Any, exc_info: bool = False) -> None:
        """
        Описание метода dta_trace:
        Логирует процесс анализа DTA.

        Аргументы:
            message (Any): Сообщение для логирования
            exc_info (bool): Включать информацию об исключении

        Возвращает:
            None
        """
        if exc_info:
            logger.debug(message, exc_info=True)
        else:
            logger.debug(message)
        self._dta_traces.append(str(message))

    async def _evaluate_configuration_cost(
        self, weighted_workload: List[Tuple[str, SelectStmt, float]], indexes: FrozenSet[IndexDefinition]
    ) -> float:
        """
        Описание метода _evaluate_configuration_cost:
        Оценивает общую стоимость конфигурации с кэшированием.

        Аргументы:
            weighted_workload (List[Tuple[str, SelectStmt, float]]): Список запросов с весами
            indexes (FrozenSet[IndexDefinition]): Множество индексов

        Возвращает:
            float: Средняя стоимость конфигурации

        Исключения:
            ValueError: При ошибке оценки конфигурации
        """
        if indexes in self.cost_cache:
            self.dta_trace(f"  - Использование кэшированной стоимости для конфигурации: {candidate_str(indexes)}")
            return self.cost_cache[indexes]

        self.dta_trace(f"  - Оценка стоимости для конфигурации: {candidate_str(indexes)}")
        total_cost: float = 0.0
        valid_queries: int = 0

        try:
            for query_text, _stmt, weight in weighted_workload:
                try:
                    plan_data = await self.get_explain_plan_with_indexes(query_text, indexes)
                    cost: float = self.extract_cost_from_json_plan(plan_data)
                    total_cost += cost * weight
                    valid_queries += 1
                except Exception as e:
                    raise ValueError(f"Ошибка при выполнении EXPLAIN для запроса: {query_text}") from e

            if valid_queries == 0:
                self.dta_trace("    + Не найдено валидных запросов для оценки стоимости")
                return float("inf")

            avg_cost: float = total_cost / valid_queries
            self.cost_cache[indexes] = avg_cost
            self.dta_trace(f"    + Стоимость конфигурации: {avg_cost:.2f} (из {valid_queries} запросов)")
            return avg_cost
        except Exception as e:
            self.dta_trace(f"    + Ошибка при оценке конфигурации: {e}")
            raise ValueError("Ошибка при оценке конфигурации") from e

    async def _estimate_index_size(self, table: str, columns: List[str]) -> int:
        """
        Описание метода _estimate_index_size:
        Оценивает размер индекса с использованием кэширования.

        Аргументы:
            table (str): Имя таблицы
            columns (List[str]): Список столбцов

        Возвращает:
            int: Оценочный размер в байтах

        Исключения:
            ValueError: При ошибке оценки размера
        """
        cache_key: Tuple[str, FrozenSet[str]] = (table, frozenset(columns))
        if cache_key in self._size_estimate_cache:
            return self._size_estimate_cache[cache_key]

        try:
            stats_query: str = """
            SELECT COALESCE(SUM(avg_width), 0) AS total_width,
                   COALESCE(SUM(n_distinct), 0) AS total_distinct
            FROM pg_stats
            WHERE tablename = {} AND attname = ANY({})
            """
            result = await SafeSqlDriver.execute_param_query(self.sql_driver, stats_query, [table, columns])
            if result and result[0].cells:
                size_estimate: int = self._estimate_index_size_internal(dict(result[0].cells))
                self._size_estimate_cache[cache_key] = size_estimate
                return size_estimate
            return 0
        except Exception as e:
            raise ValueError("Ошибка при оценке размера индекса") from e

    def _estimate_index_size_internal(self, stats: Dict[str, Any]) -> int:
        """
        Описание метода _estimate_index_size_internal:
        Внутренняя функция для оценки размера индекса.

        Аргументы:
            stats (Dict[str, Any]): Статистика столбцов

        Возвращает:
            int: Оценочный размер в байтах
        """
        width: float = (stats["total_width"] or 0) + 8
        ndistinct: float = stats["total_distinct"] or 1.0
        ndistinct = max(ndistinct, 1.0)
        size_estimate: int = int(width * ndistinct * 2.0)
        return size_estimate

    async def _format_recommendations(
        self, query_weights: List[Tuple[str, SelectStmt, float]], best_config: Tuple[Set[IndexRecommendation], float]
    ) -> List[IndexRecommendationAnalysis]:
        """
        Описание метода _format_recommendations:
        Форматирует рекомендации в список объектов IndexRecommendationAnalysis.

        Аргументы:
            query_weights (List[Tuple[str, SelectStmt, float]]): Список запросов с весами
            best_config (Tuple[Set[IndexRecommendation], float]): Лучшая конфигурация

        Возвращает:
            List[IndexRecommendationAnalysis]: Список рекомендаций
        """
        recommendations: List[IndexRecommendationAnalysis] = []
        total_size: int = 0
        budget_bytes: int = self.budget_mb * 1024 * 1024
        individual_base_cost: float = await self._evaluate_configuration_cost(query_weights, frozenset()) or 1.0
        progressive_base_cost: float = individual_base_cost
        indexes_so_far: List[IndexRecommendation] = []

        for index_config in best_config[0]:
            indexes_so_far.append(index_config)
            progressive_cost: float = await self._evaluate_configuration_cost(
                query_weights, frozenset(idx.index_definition for idx in indexes_so_far)
            )
            individual_cost: float = await self._evaluate_configuration_cost(query_weights, frozenset([index_config.index_definition]))
            size: int = await self._estimate_index_size(index_config.table, list(index_config.columns))

            if budget_bytes < 0 or total_size + size <= budget_bytes:
                self.dta_trace(f"Добавление индекса: {candidate_str([index_config])}")
                rec = IndexRecommendationAnalysis(
                    index_recommendation=IndexRecommendation(
                        table=index_config.table,
                        columns=index_config.columns,
                        using=index_config.using,
                        potential_problematic_reason=index_config.potential_problematic_reason,
                        estimated_size_bytes=size,
                    ),
                    progressive_base_cost=progressive_base_cost,
                    progressive_recommendation_cost=progressive_cost,
                    individual_base_cost=individual_base_cost,
                    individual_recommendation_cost=individual_cost,
                    queries=[q for q, _, _ in query_weights],
                    definition=index_config.definition,
                )
                progressive_base_cost = progressive_cost
                recommendations.append(rec)
                total_size += size
            else:
                self.dta_trace(f"Пропуск индекса: {candidate_str([index_config])}, так как превышен бюджет")

        return recommendations

    @staticmethod
    def extract_cost_from_json_plan(plan_data: Dict[str, Any]) -> float:
        """
        Описание метода extract_cost_from_json_plan:
        Извлекает общую стоимость из JSON-плана EXPLAIN.

        Аргументы:
            plan_data (Dict[str, Any]): Данные плана

        Возвращает:
            float: Общая стоимость

        Исключения:
            ValueError: При ошибке извлечения стоимости
        """
        try:
            if not plan_data:
                return float("inf")
            top_plan = plan_data.get("Plan")
            if not top_plan:
                logger.error("Верхний план не найден в данных плана: %s", plan_data)
                return float("inf")
            total_cost = top_plan.get("Total Cost")
            if total_cost is None:
                logger.error("Общая стоимость не найдена в верхнем плане: %s", top_plan)
                return float("inf")
            return float(total_cost)
        except (IndexError, KeyError, ValueError, json.JSONDecodeError) as e:
            raise ValueError("Ошибка при извлечении стоимости из плана") from e

    async def _get_table_size(self, table: str) -> int:
        """
        Описание метода _get_table_size:
        Получает общий размер таблицы, включая индексы и TOAST-таблицы.

        Аргументы:
            table (str): Имя таблицы

        Возвращает:
            int: Размер таблицы в байтах
        """
        if table in self._table_size_cache:
            return self._table_size_cache[table]

        try:
            query: str = "SELECT pg_total_relation_size(quote_ident({})) as rel_size"
            result = await SafeSqlDriver.execute_param_query(self.sql_driver, query, [table])
            if result and len(result) > 0 and len(result[0].cells) > 0:
                size: int = int(result[0].cells["rel_size"])
                self._table_size_cache[table] = size
                return size
            size = await self._estimate_table_size(table)
            self._table_size_cache[table] = size
            return size
        except Exception as e:
            logger.warning(f"Ошибка при получении размера таблицы {table}: {e}")
            size = await self._estimate_table_size(table)
            self._table_size_cache[table] = size
            return size

    async def _estimate_table_size(self, table: str) -> int:
        """
        Описание метода _estimate_table_size:
        Оценивает размер таблицы, если его нельзя получить из базы данных.

        Аргументы:
            table (str): Имя таблицы

        Возвращает:
            int: Оценочный размер в байтах
        """
        try:
            result = await SafeSqlDriver.execute_param_query(self.sql_driver, "SELECT count(*) as row_count FROM {}", [table])
            if result and len(result) > 0 and len(result[0].cells) > 0:
                row_count: int = int(result[0].cells["row_count"])
                return row_count * 1024
        except Exception as e:
            logger.warning(f"Ошибка при оценке размера таблицы {table}: {e}")
        return 10 * 1024 * 1024  # 10 МБ по умолчанию

    @abstractmethod
    async def _generate_recommendations(self, query_weights: List[Tuple[str, SelectStmt, float]]) -> Tuple[Set[IndexRecommendation], float]:
        """
        Описание метода _generate_recommendations:
        Абстрактный метод для генерации рекомендаций по индексам.

        Аргументы:
            query_weights (List[Tuple[str, SelectStmt, float]]): Список запросов с весами

        Возвращает:
            Tuple[Set[IndexRecommendation], float]: Множество рекомендаций и стоимость
        """
        pass
