# Анализ файла presentation.py
#
# Описание файла:
# Файл presentation.py содержит класс TextPresentation, который отвечает за текстовое представление
# рекомендаций по оптимизации индексов для PostgreSQL, полученных от IndexTuningBase.
# Класс предоставляет методы для анализа рабочей нагрузки, списка запросов или одного запроса,
# а также форматирует результаты в виде структурированного словаря с информацией о рекомендациях,
# их влиянии на запросы и улучшении производительности.
#
# Используемые модули:
# - logging: для логирования событий
# - os: для работы с переменными окружения
# - typing: для аннотаций типов
# - humanize: для форматирования размеров в читаемом виде
#
# Импорты из пакета:
# - ExplainPlanArtifact, calculate_improvement_multiple: для работы с планами выполнения и вычисления улучшений
# - SqlDriver: для взаимодействия с базой данных
# - IndexTuningBase, IndexDefinition, IndexTuningResult: для работы с рекомендациями индексов
#
# Основные компоненты:
# - Класс TextPresentation: отвечает за представление рекомендаций в текстовом формате
#
# Зависимости:
# Файл зависит от модулей artifacts, sql и dta_calc.py, index_opt_base.py.
# Использует результаты анализа из IndexTuningBase и форматирует их для вывода.

import logging
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import humanize

from ..artifacts import ExplainPlanArtifact
from ..artifacts import calculate_improvement_multiple
from ..sql import SqlDriver
from .dta_calc import IndexTuningBase
from .index_opt_base import IndexDefinition
from .index_opt_base import IndexTuningResult

# Инициализация логгера
logger = logging.getLogger(__name__)


# Описание класса TextPresentation
#
# Класс TextPresentation предоставляет текстовое представление рекомендаций по индексам,
# полученных от IndexTuningBase. Он анализирует рабочую нагрузку или конкретные запросы
# и возвращает результаты в структурированном JSON-формате.
class TextPresentation:
    """Текстовое представление рекомендаций по настройке индексов."""

    def __init__(self, sql_driver: SqlDriver, index_tuning: IndexTuningBase) -> None:
        """
        Описание метода __init__:
        Инициализирует объект TextPresentation.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для доступа к базе данных
            index_tuning (IndexTuningBase): Объект для анализа индексов

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver
        self.index_tuning: IndexTuningBase = index_tuning

    async def analyze_workload(self, max_index_size_mb: int = 10000) -> Dict[str, Any]:
        """
        Описание метода analyze_workload:
        Анализирует рабочую нагрузку запросов и рекомендует индексы.

        Аргументы:
            max_index_size_mb (int): Максимальный размер индексов в МБ (по умолчанию 10000)

        Возвращает:
            Dict[str, Any]: Словарь с рекомендациями или ошибкой
        """
        return await self._execute_analysis(
            min_calls=50,
            min_avg_time_ms=5.0,
            limit=100,
            max_index_size_mb=max_index_size_mb,
        )

    async def analyze_queries(self, queries: List[str], max_index_size_mb: int = 10000) -> Dict[str, Any]:
        """
        Описание метода analyze_queries:
        Анализирует предоставленный список SQL-запросов и рекомендует индексы.

        Аргументы:
            queries (List[str]): Список SQL-запросов для анализа
            max_index_size_mb (int): Максимальный размер индексов в МБ (по умолчанию 10000)

        Возвращает:
            Dict[str, Any]: Словарь с рекомендациями или ошибкой
        """
        if not queries:
            return {"error": "Запросы для анализа не предоставлены"}
        return await self._execute_analysis(
            query_list=queries,
            min_calls=0,
            min_avg_time_ms=0,
            limit=0,
            max_index_size_mb=max_index_size_mb,
        )

    async def analyze_single_query(self, query: str, max_index_size_mb: int = 10000) -> Dict[str, Any]:
        """
        Описание метода analyze_single_query:
        Анализирует один SQL-запрос и рекомендует индексы.

        Аргументы:
            query (str): SQL-запрос для анализа
            max_index_size_mb (int): Максимальный размер индексов в МБ (по умолчанию 10000)

        Возвращает:
            Dict[str, Any]: Словарь с рекомендациями или ошибкой
        """
        return await self._execute_analysis(
            query_list=[query],
            min_calls=0,
            min_avg_time_ms=0,
            limit=0,
            max_index_size_mb=max_index_size_mb,
        )

    async def _execute_analysis(
        self,
        query_list: Optional[List[str]] = None,
        min_calls: int = 50,
        min_avg_time_ms: float = 5.0,
        limit: int = 100,
        max_index_size_mb: int = 10000,
    ) -> Dict[str, Any]:
        """
        Описание метода _execute_analysis:
        Выполняет анализ индексов для указанной рабочей нагрузки или списка запросов.

        Аргументы:
            query_list (Optional[List[str]]): Список SQL-запросов
            min_calls (int): Минимальное количество вызовов для pg_stat_statements
            min_avg_time_ms (float): Минимальное среднее время выполнения в мс
            limit (int): Максимальное количество анализируемых запросов
            max_index_size_mb (int): Максимальный размер индексов в МБ

        Возвращает:
            Dict[str, Any]: Словарь с результатами анализа или ошибкой
        """
        try:
            session = await self.index_tuning.analyze_workload(
                query_list=query_list,
                min_calls=min_calls,
                min_avg_time_ms=min_avg_time_ms,
                limit=limit,
                max_index_size_mb=max_index_size_mb,
            )

            include_langfuse_trace: bool = os.environ.get("POSTGRES_MCP_INCLUDE_LANGFUSE_TRACE", "true").lower() == "true"
            langfuse_trace: Dict[str, List[str]] = {"_langfuse_trace": session.dta_traces} if include_langfuse_trace else {}

            if session.error:
                return {"error": session.error, **langfuse_trace}

            if not session.recommendations:
                return {"recommendations": "Рекомендации по индексам не найдены.", **langfuse_trace}

            total_size_bytes: int = sum(rec.estimated_size_bytes for rec in session.recommendations)
            initial_cost: float = session.recommendations[0].progressive_base_cost if session.recommendations else 0
            new_cost: float = session.recommendations[-1].progressive_recommendation_cost if session.recommendations else 1.0
            improvement_multiple: float = calculate_improvement_multiple(initial_cost, new_cost)

            recommendations: List[Dict[str, Any]] = self._build_recommendations_list(session)
            query_impact: List[Dict[str, Any]] = await self._generate_query_impact(session)

            return {
                "summary": {
                    "total_recommendations": len(session.recommendations),
                    "base_cost": f"{initial_cost:.1f}",
                    "new_cost": f"{new_cost:.1f}",
                    "total_size_bytes": humanize.naturalsize(total_size_bytes),
                    "improvement_multiple": f"{improvement_multiple:.1f}",
                },
                "recommendations": recommendations,
                "query_impact": query_impact,
                **langfuse_trace,
            }
        except Exception as e:
            logger.error(f"Ошибка при анализе запросов: {e}", exc_info=True)
            return {"error": f"Ошибка при анализе запросов: {e}"}

    def _build_recommendations_list(self, session: IndexTuningResult) -> List[Dict[str, Any]]:
        """
        Описание метода _build_recommendations_list:
        Формирует список рекомендаций по индексам из результатов сессии.

        Аргументы:
            session (IndexTuningResult): Объект сессии с результатами анализа

        Возвращает:
            List[Dict[str, Any]]: Список словарей с информацией о рекомендациях
        """
        recommendations: List[Dict[str, Any]] = []
        for index_apply_order, rec in enumerate(session.recommendations):
            rec_dict: Dict[str, Any] = {
                "index_apply_order": index_apply_order + 1,
                "index_target_table": rec.table,
                "index_target_columns": rec.columns,
                "benefit_of_this_index_only": {
                    "improvement_multiple": f"{rec.individual_improvement_multiple:.1f}",
                    "base_cost": f"{rec.individual_base_cost:.1f}",
                    "new_cost": f"{rec.individual_recommendation_cost:.1f}",
                },
                "benefit_after_previous_indexes": {
                    "improvement_multiple": f"{rec.progressive_improvement_multiple:.1f}",
                    "base_cost": f"{rec.progressive_base_cost:.1f}",
                    "new_cost": f"{rec.progressive_recommendation_cost:.1f}",
                },
                "index_estimated_size": humanize.naturalsize(rec.estimated_size_bytes),
                "index_definition": rec.definition,
            }
            if rec.potential_problematic_reason == "long_text_column":
                rec_dict["warning"] = (
                    "Этот индекс потенциально проблемный, так как включает длинный текстовый столбец. "
                    "Создание этого индекса может быть невозможно, если размер строки индекса слишком велик "
                    "(т.е. более 8191 байт)."
                )
            elif rec.potential_problematic_reason:
                rec_dict["warning"] = f"Этот индекс потенциально проблемный, так как включает столбец типа {rec.potential_problematic_reason}."
            recommendations.append(rec_dict)
        return recommendations

    async def _generate_query_impact(self, session: IndexTuningResult) -> List[Dict[str, Any]]:
        """
        Описание метода _generate_query_impact:
        Генерирует раздел влияния на запросы с планами выполнения до и после применения индексов.

        Аргументы:
            session (IndexTuningResult): Объект сессии с результатами анализа

        Возвращает:
            List[Dict[str, Any]]: Список словарей с информацией о влиянии на запросы
        """
        query_impact: List[Dict[str, Any]] = []
        if not session.recommendations:
            return query_impact

        workload_queries: List[str] = session.recommendations[0].queries
        seen: Set[str] = set()
        unique_queries: List[str] = [q for q in workload_queries if not (q in seen or seen.add(q))]

        if unique_queries and self.index_tuning:
            for query in unique_queries:
                before_plan: Dict[str, Any] = await self.index_tuning.get_explain_plan_with_indexes(query, frozenset())
                index_configs: FrozenSet[IndexDefinition] = frozenset(
                    IndexDefinition(rec.table, rec.columns, rec.using) for rec in session.recommendations
                )
                after_plan: Dict[str, Any] = await self.index_tuning.get_explain_plan_with_indexes(query, index_configs)

                base_cost: float = self.index_tuning.extract_cost_from_json_plan(before_plan)
                new_cost: float = self.index_tuning.extract_cost_from_json_plan(after_plan)

                improvement_multiple: str = "∞" if new_cost == 0 or base_cost == 0 else f"{calculate_improvement_multiple(base_cost, new_cost):.1f}"

                before_plan_text: str = ExplainPlanArtifact.format_plan_summary(before_plan)
                after_plan_text: str = ExplainPlanArtifact.format_plan_summary(after_plan)
                diff_text: str = ExplainPlanArtifact.create_plan_diff(before_plan, after_plan)

                query_impact.append(
                    {
                        "query": query,
                        "base_cost": f"{base_cost:.1f}",
                        "new_cost": f"{new_cost:.1f}",
                        "improvement_multiple": improvement_multiple,
                        "before_explain_plan": "```\n" + before_plan_text + "\n```",
                        "after_explain_plan": "```\n" + after_plan_text + "\n```",
                        "explain_plan_diff": "```\n" + diff_text + "\n```",
                    }
                )

        return query_impact
