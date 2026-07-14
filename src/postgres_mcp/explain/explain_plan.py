# Анализ файла explain_plan.py
#
# Описание файла:
# Файл explain_plan.py содержит класс ExplainPlanTool, который предоставляет функциональность для генерации и анализа
# планов выполнения (EXPLAIN) в PostgreSQL. Класс поддерживает генерацию планов с использованием гипотетических индексов
# через расширение HypoPG, а также обработку запросов с параметрами (bind variables). Он используется для оценки
# производительности запросов и формирования рекомендаций по индексам.
#
# Используемые модули:
# - logging: для логирования событий
# - re: для работы с регулярными выражениями
# - typing: для аннотаций типов и проверки типов
# - __future__.annotations: для поддержки аннотаций типов в будущем стиле
#
# Импорты из пакета:
# - ErrorResult, ExplainPlanArtifact: для обработки результатов и артефактов планов выполнения
# - IndexDefinition, SafeSqlDriver, SqlBindParams, SqlDriver, check_postgres_version_requirement:
#   для работы с SQL, индексами и проверкой версий PostgreSQL
#
# Основные компоненты:
# - Класс ExplainPlanTool: основной класс для генерации и анализа планов выполнения
#
# Зависимости:
# Файл зависит от модулей artifacts и sql (sql_driver.py, safe_sql.py и др.).
# Также требуется расширение HypoPG для работы с гипотетическими индексами.
#
# Примечания:
# - Код игнорирует проверку длины строк (E501) с помощью ruff.
# - Используется TYPE_CHECKING для условного импорта типов, чтобы избежать циклических зависимостей.

# Игнорирование проверки длины строк для ruff

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import Dict
from typing import FrozenSet
from typing import List
from typing import Tuple

from ..artifacts import ErrorResult
from ..artifacts import ExplainPlanArtifact
from ..sql import IndexDefinition
from ..sql import SafeSqlDriver
from ..sql import SqlBindParams
from ..sql import check_postgres_version_requirement

# Инициализация логгера
logger = logging.getLogger(__name__)

# Условный импорт для проверки типов
if TYPE_CHECKING:
    from ..sql.sql_driver import SqlDriver


# Описание класса ExplainPlanTool
#
# Класс ExplainPlanTool предоставляет инструменты для генерации и анализа планов выполнения PostgreSQL.
# Поддерживает стандартные EXPLAIN, EXPLAIN ANALYZE и планы с гипотетическими индексами.
class ExplainPlanTool:
    """Инструмент для генерации и анализа планов выполнения PostgreSQL."""

    def __init__(self, sql_driver: SqlDriver) -> None:
        """
        Описание метода __init__:
        Инициализирует объект ExplainPlanTool.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для взаимодействия с базой данных

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver

    async def replace_query_parameters_if_needed(self, sql_query: str) -> Tuple[str, bool]:
        """
        Описание метода replace_query_parameters_if_needed:
        Заменяет параметры запроса (bind variables) на тестовые значения, если необходимо.

        Аргументы:
            sql_query (str): SQL-запрос для обработки

        Возвращает:
            Tuple[str, bool]: Модифицированный запрос и флаг использования GENERIC_PLAN
        """
        use_generic_plan: bool = False
        has_bind_variables: bool = self._has_bind_variables(sql_query)

        if has_bind_variables:
            has_like: bool = self._has_like_expressions(sql_query)
            meets_pg_version_requirement, _message = await check_postgres_version_requirement(
                self.sql_driver, min_version=16, feature_name="Generic plan with bind variables ($1, $2, etc.)"
            )

            if not meets_pg_version_requirement or has_like:
                logger.debug("Замена параметров запроса на тестовые значения")
                if meets_pg_version_requirement and has_like:
                    logger.debug("Обнаружены выражения LIKE, используется замена параметров вместо GENERIC_PLAN")
                bind_params = SqlBindParams(self.sql_driver)
                modified_query: str = await bind_params.replace_parameters(sql_query)
                logger.debug(f"Исходный запрос: {sql_query}")
                logger.debug(f"Модифицированный запрос: {modified_query}")
                sql_query = modified_query
            else:
                use_generic_plan = True

        return sql_query, use_generic_plan

    async def explain(self, sql_query: str, do_analyze: bool = False) -> ExplainPlanArtifact | ErrorResult:
        """
        Описание метода explain:
        Генерирует план выполнения (EXPLAIN) для SQL-запроса.

        Аргументы:
            sql_query (str): SQL-запрос для анализа
            do_analyze (bool): Включать ли режим ANALYZE (по умолчанию False)

        Возвращает:
            ExplainPlanArtifact | ErrorResult: Артефакт плана или результат ошибки
        """
        modified_sql_query, use_generic_plan = await self.replace_query_parameters_if_needed(sql_query)
        return await self._run_explain_query(modified_sql_query, analyze=do_analyze, generic_plan=use_generic_plan)

    async def explain_analyze(self, sql_query: str) -> ExplainPlanArtifact | ErrorResult:
        """
        Описание метода explain_analyze:
        Генерирует план выполнения с анализом (EXPLAIN ANALYZE) для SQL-запроса.

        Аргументы:
            sql_query (str): SQL-запрос для анализа

        Возвращает:
            ExplainPlanArtifact | ErrorResult: Артефакт плана или результат ошибки
        """
        return await self.explain(sql_query, do_analyze=True)

    async def explain_with_hypothetical_indexes(
        self, sql_query: str, hypothetical_indexes: List[Dict[str, Any]]
    ) -> ExplainPlanArtifact | ErrorResult:
        """
        Описание метода explain_with_hypothetical_indexes:
        Генерирует план выполнения для запроса с учетом гипотетических индексов.

        Аргументы:
            sql_query (str): SQL-запрос для анализа
            hypothetical_indexes (List[Dict[str, Any]]): Список определений гипотетических индексов

        Возвращает:
            ExplainPlanArtifact | ErrorResult: Артефакт плана или результат ошибки
        """
        try:
            if not isinstance(hypothetical_indexes, list):
                return ErrorResult(f"Ожидался список определений индексов, получен {type(hypothetical_indexes)}")

            for idx in hypothetical_indexes:
                if not isinstance(idx, dict):
                    return ErrorResult(f"Ожидался словарь для определения индекса, получен {type(idx)}")
                if "table" not in idx:
                    return ErrorResult("Отсутствует 'table' в определении индекса")
                if "columns" not in idx:
                    return ErrorResult("Отсутствует 'columns' в определении индекса")
                if not isinstance(idx["columns"], list):
                    try:
                        idx["columns"] = list(idx["columns"]) if hasattr(idx["columns"], "__iter__") else [idx["columns"]]
                    except Exception as e:
                        return ErrorResult(f"Ожидался список для 'columns', получен {type(idx['columns'])}: {e}")

            indexes: FrozenSet[IndexDefinition] = frozenset(
                IndexDefinition(
                    table=idx["table"],
                    columns=tuple(idx["columns"]),
                    using=idx.get("using", "btree"),
                )
                for idx in hypothetical_indexes
            )

            modified_sql_query, use_generic_plan = await self.replace_query_parameters_if_needed(sql_query)
            plan_data: Dict[str, Any] = await self.generate_explain_plan_with_hypothetical_indexes(modified_sql_query, indexes, use_generic_plan)

            if not plan_data or not isinstance(plan_data, dict) or "Plan" not in plan_data:
                return ErrorResult("Не удалось сгенерировать валидный план выполнения с гипотетическими индексами")

            try:
                return ExplainPlanArtifact.from_json_data(plan_data)
            except Exception as e:
                return ErrorResult(f"Ошибка при преобразовании плана выполнения: {e}")

        except Exception as e:
            logger.error(f"Ошибка в explain_with_hypothetical_indexes: {e}", exc_info=True)
            return ErrorResult(f"Ошибка при генерации плана выполнения с гипотетическими индексами: {e}")

    def _has_bind_variables(self, query: str) -> bool:
        """
        Описание метода _has_bind_variables:
        Проверяет, содержит ли запрос параметры (bind variables, например, $1, $2).

        Аргументы:
            query (str): SQL-запрос для проверки

        Возвращает:
            bool: True, если запрос содержит параметры
        """
        return bool(re.search(r"\$\d+", query))

    def _has_like_expressions(self, query: str) -> bool:
        """
        Описание метода _has_like_expressions:
        Проверяет, содержит ли запрос выражения LIKE, которые не поддерживаются в GENERIC_PLAN.

        Аргументы:
            query (str): SQL-запрос для проверки

        Возвращает:
            bool: True, если запрос содержит выражения LIKE
        """
        return bool(re.search(r"\bLIKE\b", query, re.IGNORECASE))

    async def _run_explain_query(self, query: str, analyze: bool = False, generic_plan: bool = False) -> ExplainPlanArtifact | ErrorResult:
        """
        Описание метода _run_explain_query:
        Выполняет запрос EXPLAIN для указанного SQL-запроса.

        Аргументы:
            query (str): SQL-запрос для анализа
            analyze (bool): Включать ли режим ANALYZE
            generic_plan (bool): Использовать ли GENERIC_PLAN

        Возвращает:
            ExplainPlanArtifact | ErrorResult: Артефакт плана или результат ошибки
        """
        try:
            explain_options: List[str] = ["FORMAT JSON"]
            if analyze:
                explain_options.append("ANALYZE")
            if generic_plan:
                explain_options.append("GENERIC_PLAN")

            explain_q: str = f"EXPLAIN ({', '.join(explain_options)}) {query}"
            logger.debug(f"ВЫПОЛНЕНИЕ ЗАПРОСА EXPLAIN: {explain_q}")
            rows = await self.sql_driver.execute_query(explain_q)
            if rows is None:
                return ErrorResult("EXPLAIN не вернул результатов")

            query_plan_data = rows[0].cells["QUERY PLAN"]
            if not isinstance(query_plan_data, list):
                return ErrorResult(f"Ожидался список от EXPLAIN, получен {type(query_plan_data)}")
            if len(query_plan_data) == 0:
                return ErrorResult("EXPLAIN не вернул результатов")

            plan_dict: Dict[str, Any] = query_plan_data[0]
            if not isinstance(plan_dict, dict):
                return ErrorResult(f"Ожидался словарь в результате EXPLAIN, получен {type(plan_dict)} с значением {plan_dict}")

            try:
                return ExplainPlanArtifact.from_json_data(plan_dict)
            except Exception as e:
                return ErrorResult(f"Внутренняя ошибка при преобразовании плана выполнения: {e}")
        except Exception as e:
            return ErrorResult(f"Ошибка при выполнении плана выполнения: {e}")

    async def generate_explain_plan_with_hypothetical_indexes(
        self,
        query_text: str,
        indexes: FrozenSet[IndexDefinition],
        use_generic_plan: bool = False,
        dta: Any = None,
    ) -> Dict[str, Any]:
        """
        Описание метода generate_explain_plan_with_hypothetical_indexes:
        Генерирует план выполнения для запроса с указанными гипотетическими индексами.

        Аргументы:
            query_text (str): SQL-запрос для анализа
            indexes (FrozenSet[IndexDefinition]): Множество гипотетических индексов
            use_generic_plan (bool): Использовать ли GENERIC_PLAN
            dta (Any): Объект для трассировки (опционально)

        Возвращает:
            Dict[str, Any]: План выполнения в виде словаря

        Исключения:
            Exception: При ошибке выполнения запроса
        """
        try:
            create_indexes_query: str = "SELECT hypopg_reset();"
            if indexes:
                create_indexes_query += SafeSqlDriver.param_sql_to_query(
                    "SELECT hypopg_create_index({});" * len(indexes),
                    [idx.definition for idx in indexes],
                )

            explain_options: List[str] = ["FORMAT JSON"]
            if use_generic_plan:
                explain_options.append("GENERIC_PLAN")
            if indexes:
                explain_options.append("COSTS TRUE")

            explain_plan_query: str = f"{create_indexes_query}EXPLAIN ({', '.join(explain_options)}) {query_text}"
            plan_result = await self.sql_driver.execute_query(explain_plan_query)

            if plan_result and plan_result[0].cells.get("QUERY PLAN"):
                plan_data = plan_result[0].cells.get("QUERY PLAN")
                if isinstance(plan_data, list) and len(plan_data) > 0:
                    return plan_data[0]
                else:
                    if dta:
                        dta.dta_trace(f"      - plan_data пустой список с типом: {type(plan_data)}")

            if dta:
                dta.dta_trace("      - возвращается пустой план")
            return {"Plan": {"Total Cost": float("inf")}}

        except Exception as e:
            logger.error(f"Ошибка при получении плана выполнения для запроса: {query_text} с ошибкой: {e}", exc_info=True)
            raise e
