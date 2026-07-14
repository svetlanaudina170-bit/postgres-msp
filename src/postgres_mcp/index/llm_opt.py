# Анализ файла llm_opt.py
#
# Описание файла:
# Файл llm_opt.py содержит класс LLMOptimizerTool, который реализует оптимизацию индексов для PostgreSQL
# с использованием языковой модели (LLM). Класс наследуется от IndexTuningBase и использует LLM для
# генерации альтернативных конфигураций индексов, которые затем оцениваются с помощью гипотетических
# индексов HypoPG. Также включает вспомогательные классы и функции для работы с индексами и их оценкой.
#
# Используемые модули:
# - logging: для логирования событий
# - math: для математических вычислений (логарифмы в оценке)
# - dataclasses: для создания классов данных
# - typing: для аннотаций типов
# - instructor: для взаимодействия с LLM через структурированные модели
# - openai: для работы с API OpenAI
# - pglast.ast: для работы с AST-узлами SQL-запросов
# - pydantic: для создания моделей данных
#
# Импорты из пакета:
# - ErrorResult, ExplainPlanTool: для работы с планами выполнения и ошибками
# - TableAliasVisitor, IndexDefinition, SqlDriver: для работы с SQL и базой данных
# - IndexRecommendation, IndexTuningBase: для работы с рекомендациями индексов
#
# Основные компоненты:
# - Класс Index: Pydantic-модель для представления индекса
# - Класс IndexingAlternative: Pydantic-модель для набора альтернативных индексов
# - Класс ScoredIndexes: Класс данных для хранения оцененных конфигураций индексов
# - Класс LLMOptimizerTool: Основной класс для оптимизации индексов с использованием LLM
#
# Зависимости:
# Файл зависит от модулей postgres_mcp (artifacts, explain, sql) и index_opt_base.py.
# Использует HypoPG для оценки гипотетических индексов и API OpenAI для генерации рекомендаций.

import logging
import math
from dataclasses import dataclass
from typing import Any, FrozenSet, List, Set, Tuple, override

import instructor
from openai import OpenAI
from pglast.ast import SelectStmt
from pydantic import BaseModel

from postgres_mcp.artifacts import ErrorResult
from postgres_mcp.explain.explain_plan import ExplainPlanTool
from postgres_mcp.sql import TableAliasVisitor

from ..sql import IndexDefinition, SqlDriver
from .index_opt_base import IndexRecommendation, IndexTuningBase

# Инициализация логгера
logger = logging.getLogger(__name__)


# Описание класса Index
#
# Класс Index — это Pydantic-модель, используемая для взаимодействия с LLM через библиотеку instructor.
# Представляет индекс с именем таблицы и списком столбцов.
class Index(BaseModel):
    table_name: str
    columns: Tuple[str, ...]

    def __hash__(self) -> int:
        """
        Описание метода __hash__:
        Вычисляет хэш объекта на основе имени таблицы и столбцов.

        Возвращает:
            int: Хэш объекта
        """
        return hash((self.table_name, self.columns))

    def __eq__(self, other: Any) -> bool:
        """
        Описание метода __eq__:
        Сравнивает два объекта Index на равенство.

        Аргументы:
            other (Any): Объект для сравнения

        Возвращает:
            bool: True, если объекты равны
        """
        if not isinstance(other, Index):
            return False
        return self.table_name == other.table_name and self.columns == other.columns

    def to_index_recommendation(self) -> IndexRecommendation:
        """
        Описание метода to_index_recommendation:
        Преобразует объект Index в IndexRecommendation.

        Возвращает:
            IndexRecommendation: Объект рекомендации индекса
        """
        return IndexRecommendation(table=self.table_name, columns=self.columns)

    def to_index_definition(self) -> IndexDefinition:
        """
        Описание метода to_index_definition:
        Преобразует объект Index в IndexDefinition.

        Возвращает:
            IndexDefinition: Объект определения индекса
        """
        return IndexDefinition(table=self.table_name, columns=self.columns)


# Описание класса IndexingAlternative
#
# Класс IndexingAlternative — это Pydantic-модель, представляющая список альтернативных
# наборов индексов, предложенных LLM.
class IndexingAlternative(BaseModel):
    alternatives: List[Set[Index]]


# Описание класса ScoredIndexes
#
# Класс ScoredIndexes хранит информацию об оцененной конфигурации индексов,
# включая стоимость выполнения, размер индексов и итоговую оценку.
@dataclass
class ScoredIndexes:
    indexes: Set[Index]
    execution_cost: float
    index_size: float
    objective_score: float


# Описание класса LLMOptimizerTool
#
# Класс LLMOptimizerTool наследуется от IndexTuningBase и реализует оптимизацию индексов
# с использованием LLM. Он генерирует альтернативные конфигурации индексов, оценивает их
# с помощью HypoPG и выбирает лучшую на основе целевой функции Парето.
class LLMOptimizerTool(IndexTuningBase):
    def __init__(
        self,
        sql_driver: SqlDriver,
        max_no_progress_attempts: int = 5,
        pareto_alpha: float = 2.0,
    ) -> None:
        """
        Описание метода __init__:
        Инициализирует LLMOptimizerTool с параметрами оптимизации.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для доступа к базе данных
            max_no_progress_attempts (int): Максимальное количество попыток без улучшения
            pareto_alpha (float): Вес размера индекса в целевой функции Парето

        Возвращает:
            None
        """
        super().__init__(sql_driver)
        self.sql_driver: SqlDriver = sql_driver
        self.max_no_progress_attempts: int = max_no_progress_attempts
        self.pareto_alpha: float = pareto_alpha
        logger.info("Инициализирован LLMOptimizerTool с max_no_progress_attempts=%d", max_no_progress_attempts)

    def score(self, execution_cost: float, index_size: float) -> float:
        """
        Описание метода score:
        Вычисляет оценку конфигурации на основе стоимости выполнения и размера индекса.

        Аргументы:
            execution_cost (float): Стоимость выполнения запроса
            index_size (float): Размер индексов в байтах

        Возвращает:
            float: Оценка конфигурации
        """
        return math.log(execution_cost) + self.pareto_alpha * math.log(index_size)

    @override
    async def _generate_recommendations(self, query_weights: List[Tuple[str, SelectStmt, float]]) -> Tuple[Set[IndexRecommendation], float]:
        """
        Описание метода _generate_recommendations:
        Генерирует рекомендации по индексам, используя LLM.

        Аргументы:
            query_weights (List[Tuple[str, SelectStmt, float]]): Список запросов с весами

        Возвращает:
            Tuple[Set[IndexRecommendation], float]: Множество рекомендаций и стоимость

        Исключения:
            ValueError: Если передан более одного запроса
        """
        if len(query_weights) > 1:
            logger.error("Оптимизация LLM поддерживает только один запрос за раз")
            raise ValueError("Оптимизация LLM поддерживает только один запрос за раз.")

        query: str = query_weights[0][0]
        parsed_query: SelectStmt = query_weights[0][1]
        logger.info("Генерация рекомендаций по индексам для запроса: %s", query)

        table_visitor = TableAliasVisitor()
        table_visitor(parsed_query)
        tables: Set[str] = table_visitor.tables
        logger.info("Извлеченные таблицы из запроса: %s", tables)

        table_sizes: Dict[str, int] = {table: await self._get_table_size(table) for table in tables}
        total_table_size: int = sum(table_sizes.values())
        logger.info("Общий размер таблиц: %s", total_table_size)

        explain_tool = ExplainPlanTool(self.sql_driver)
        explain_result = await explain_tool.explain(query)
        if isinstance(explain_result, ErrorResult):
            logger.error("Не удалось сгенерировать план выполнения: %s", explain_result.to_text())
            raise ValueError(f"Не удалось сгенерировать план выполнения: {explain_result.to_text()}")

        explain_plan_json: Dict[str, Any] = explain_result.value
        logger.debug("Сгенерирован план выполнения: %s", explain_plan_json)

        indexes_used: Set[Index] = await self._extract_indexes_from_explain_plan_with_columns(explain_plan_json)
        original_cost: float = await self._evaluate_configuration_cost(query_weights, frozenset())
        logger.info("Исходная стоимость запроса: %f", original_cost)

        original_config = ScoredIndexes(
            indexes=indexes_used,
            execution_cost=original_cost,
            index_size=total_table_size,
            objective_score=self.score(original_cost, total_table_size),
        )
        best_config: ScoredIndexes = original_config
        attempt_history: List[ScoredIndexes] = [original_config]
        no_progress_count: int = 0
        client = instructor.from_openai(OpenAI())

        score: float = self.score(original_cost, total_table_size)
        logger.info("Начальная оценка: %f", score)

        while no_progress_count < self.max_no_progress_attempts:
            logger.info("Запрос рекомендаций по индексам от LLM")

            history_prompt: str = ""
            if attempt_history:
                history_prompt = "\nПредыдущие попытки и их стоимости:\n"
                for attempt in attempt_history:
                    indexes_str: str = ";".join(idx.to_index_definition().definition for idx in attempt.indexes)
                    history_prompt += (
                        f"- Индексы: {indexes_str}, Стоимость: {attempt.execution_cost}, "
                        f"Размер индексов: {attempt.index_size}, Оценка: {attempt.objective_score}\n"
                    )

            remaining_attempts_prompt: str = ""
            if no_progress_count > 0:
                remaining_attempts_prompt = f"Совершено {no_progress_count} попыток без улучшения. "
                if self.max_no_progress_attempts - no_progress_count < self.max_no_progress_attempts / 2:
                    remaining_attempts_prompt += "Будьте креативны и предложите неочевидные индексы."

            response = client.chat.completions.create(
                model="gpt-4o",
                response_model=IndexingAlternative,
                temperature=1.2,
                messages=[
                    {"role": "system", "content": "Вы помощник, генерирующий рекомендации по индексам для рабочей нагрузки."},
                    {
                        "role": "user",
                        "content": (
                            f"Вот запрос, который мы оптимизируем: {query}\n"
                            f"Вот план выполнения: {explain_plan_json}\n"
                            f"Вот существующие индексы: {';'.join(idx.to_index_definition().definition for idx in indexes_used)}\n"
                            f"{history_prompt}\n"
                            "Каждая предложенная комбинация индексов оценивается с помощью HypoPG. "
                            "Общая оценка основана на комбинации стоимости выполнения и размера индексов. "
                            "Меньшие значения лучше. Предпочитайте меньше индексов и индексы с меньшим количеством столбцов. "
                            f"{remaining_attempts_prompt}"
                        ),
                    },
                ],
            )

            index_alternatives: List[Set[Index]] = response.alternatives
            logger.info("Получено %d альтернативных конфигураций индексов от LLM", len(index_alternatives))

            if not index_alternatives:
                logger.warning("LLM не сгенерировал альтернативные индексы")
                break

            found_improvement: bool = False
            for i, index_set in enumerate(index_alternatives):
                try:
                    logger.info("Оценка альтернативы %d/%d с %d индексами", i + 1, len(index_alternatives), len(index_set))
                    execution_cost_estimate: float = await self._evaluate_configuration_cost(
                        query_weights, frozenset({index.to_index_definition() for index in index_set})
                    )
                    logger.info(
                        "Стоимость альтернативы %d: %f (уменьшение: %.2f%%)",
                        i + 1,
                        execution_cost_estimate,
                        ((best_config.execution_cost - execution_cost_estimate) / best_config.execution_cost) * 100,
                    )

                    index_size_estimate: float = await self._estimate_index_size_2({index.to_index_definition() for index in index_set}, 1024 * 1024)
                    logger.info("Оценочный размер индексов: %f", index_size_estimate)

                    score = math.log(execution_cost_estimate) + self.pareto_alpha * math.log(total_table_size + index_size_estimate)
                    latest_config = ScoredIndexes(
                        indexes={Index(table_name=index.table_name, columns=index.columns) for index in index_set},
                        execution_cost=execution_cost_estimate,
                        index_size=index_size_estimate,
                        objective_score=score,
                    )
                    attempt_history.append(latest_config)
                    logger.info("Последняя конфигурация: %s", latest_config)

                    if latest_config.objective_score < best_config.objective_score:
                        best_config = latest_config
                        found_improvement = True
                except Exception as e:
                    logger.error("Ошибка при оценке альтернативы %d/%d: %s", i + 1, len(index_alternatives), str(e))

            attempt_history.sort(key=lambda x: x.objective_score)
            attempt_history = attempt_history[:5]

            if found_improvement:
                no_progress_count = 0
            else:
                no_progress_count += 1
                logger.info(
                    "Улучшение не найдено в этой итерации. Попытки без прогресса: %d/%d",
                    no_progress_count,
                    self.max_no_progress_attempts,
                )

        if best_config != original_config:
            logger.info(
                "Выбрана лучшая конфигурация индексов с %d индексами, уменьшение стоимости: %.2f%%, индексы: %s",
                len(best_config.indexes),
                ((original_cost - best_config.execution_cost) / original_cost) * 100,
                ", ".join(f"{idx.table_name}.({','.join(idx.columns)})" for idx in best_config.indexes),
            )
        else:
            logger.info("Лучшая конфигурация индексов не найдена")

        best_index_config_set: Set[IndexRecommendation] = {index.to_index_recommendation() for index in best_config.indexes}
        return (best_index_config_set, best_config.execution_cost)

    async def _estimate_index_size_2(self, index_set: Set[IndexDefinition], min_size_penalty: float = 1024 * 1024) -> float:
        """
        Описание метода _estimate_index_size_2:
        Оценивает размер набора индексов с использованием HypoPG.

        Аргументы:
            index_set (Set[IndexDefinition]): Множество объектов IndexDefinition
            min_size_penalty (float): Минимальный штраф за размер (по умолчанию 1 МБ)

        Возвращает:
            float: Общий оценочный размер индексов в байтах
        """
        if not index_set:
            return 0.0

        total_size: float = 0.0
        for index_config in index_set:
            try:
                create_index_query: str = (
                    "WITH hypo_index AS (SELECT indexrelid FROM hypopg_create_index(%s)) "
                    "SELECT hypopg_relation_size(indexrelid) as size, hypopg_drop_index(indexrelid) FROM hypo_index;"
                )
                result = await self.sql_driver.execute_query(create_index_query, params=[index_config.definition])
                if result and len(result) > 0:
                    size: float = result[0].cells.get("size", 0)
                    total_size += max(float(size), min_size_penalty)
                    logger.debug(f"Оценочный размер индекса {index_config.name}: {size} байт")
                else:
                    logger.warning(f"Не удалось оценить размер индекса {index_config.name}")
            except Exception as e:
                logger.error(f"Ошибка при оценке размера индекса {index_config.name}: {e!s}")

        return total_size

    def _extract_indexes_from_explain_plan(self, explain_plan_json: Any) -> Set[Tuple[str, str]]:
        """
        Описание метода _extract_indexes_from_explain_plan:
        Извлекает индексы, использованные в JSON-плане выполнения.

        Аргументы:
            explain_plan_json (Any): JSON-план выполнения PostgreSQL

        Возвращает:
            Set[Tuple[str, str]]: Множество кортежей (имя_таблицы, имя_индекса)
        """
        indexes_used: Set[Tuple[str, str]] = set()
        if isinstance(explain_plan_json, dict):
            plan_data = explain_plan_json.get("Plan")
            if plan_data is not None:

                def extract_indexes_from_node(node: Dict[str, Any]) -> None:
                    if node.get("Node Type") in ["Index Scan", "Index Only Scan", "Bitmap Index Scan"]:
                        if "Index Name" in node and "Relation Name" in node:
                            indexes_used.add((node["Relation Name"], node["Index Name"]))
                    if "Plans" in node:
                        for child in node["Plans"]:
                            extract_indexes_from_node(child)

                extract_indexes_from_node(plan_data)
                logger.info("Извлечено %d индексов из плана выполнения", len(indexes_used))

        return indexes_used

    async def _extract_indexes_from_explain_plan_with_columns(self, explain_plan_json: Any) -> Set[Index]:
        """
        Описание метода _extract_indexes_from_explain_plan_with_columns:
        Извлекает индексы с их столбцами из JSON-плана выполнения.

        Аргументы:
            explain_plan_json (Any): JSON-план выполнения PostgreSQL

        Возвращает:
            Set[Index]: Множество объектов Index с именами таблиц и столбцов
        """
        index_tuples = self._extract_indexes_from_explain_plan(explain_plan_json)
        indexes_with_columns: Set[Index] = set()
        for table_name, index_name in index_tuples:
            columns = await self._get_index_columns(index_name)
            index_with_columns = Index(table_name=table_name, columns=columns)
            indexes_with_columns.add(index_with_columns)

        return indexes_with_columns

    async def _get_index_columns(self, index_name: str) -> Tuple[str, ...]:
        """
        Описание метода _get_index_columns:
        Получает столбцы для указанного индекса из базы данных.

        Аргументы:
            index_name (str): Имя индекса

        Возвращает:
            Tuple[str, ...]: Кортеж имен столбцов

        Исключения:
            ValueError: При ошибке получения столбцов
        """
        try:
            query: str = """
            SELECT a.attname
            FROM pg_index i
            JOIN pg_class c ON c.oid = i.indexrelid
            JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
            WHERE c.relname = %s
            ORDER BY array_position(i.indkey, a.attnum)
            """
            result = await self.sql_driver.execute_query(query, [index_name])
            if result and len(result) > 0:
                columns: List[str] = [row.cells.get("attname", "") for row in result if row.cells.get("attname")]
                return tuple(columns)
            logger.warning(f"Столбцы для индекса {index_name} не найдены")
            return tuple()
        except Exception as e:
            logger.error(f"Ошибка при получении столбцов для индекса {index_name}: {e!s}")
            return tuple()
