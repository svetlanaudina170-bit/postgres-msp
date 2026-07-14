# Анализ файла dta_calc.py
#
# Описание файла:
# Файл dta_calc.py содержит классы DatabaseTuningAdvisor и ConditionColumnCollector, которые реализуют
# функциональность для оптимизации индексов в PostgreSQL. DatabaseTuningAdvisor использует гибридный
# подход "seed + greedy" для генерации рекомендаций по индексам на основе рабочей нагрузки запросов,
# учитывая бюджет хранения, ограничения по времени и использованию столбцов. ConditionColumnCollector
# собирает столбцы, используемые в условиях запросов (WHERE, JOIN, HAVING), с учетом псевдонимов таблиц.
#
# Используемые модули:
# - logging: для логирования событий
# - time: для отслеживания времени выполнения
# - itertools.combinations: для генерации комбинаций столбцов
# - typing: для аннотаций типов
# - humanize: для форматирования размеров в читаемом виде
# - pglast.ast: для работы с AST-узлами SQL-запросов
#
# Импорты из пакета:
# - ColumnCollector, SafeSqlDriver, SqlDriver, TableAliasVisitor: для работы с SQL и базой данных
# - IndexRecommendation, IndexTuningBase, candidate_str, pp_list: для работы с рекомендациями индексов
#
# Основные компоненты:
# - Класс DatabaseTuningAdvisor: реализует логику оптимизации индексов
# - Класс ConditionColumnCollector: собирает столбцы из условий запросов
#
# Зависимости:
# Файл зависит от модулей sql (safe_sql.py, sql_driver.py), index_opt_base и других частей пакета,
# взаимодействующих с PostgreSQL и анализом запросов.

import logging
import time
from itertools import combinations
from typing import Optional, Any, Dict, FrozenSet, List, Set, Tuple, override

import humanize
from pglast.ast import ColumnRef, JoinExpr, Node, SelectStmt

from ..sql import ColumnCollector, SafeSqlDriver, SqlDriver, TableAliasVisitor
from .index_opt_base import IndexRecommendation, IndexTuningBase, candidate_str, pp_list

# Инициализация логгера
logger = logging.getLogger(__name__)

# Описание класса DatabaseTuningAdvisor
#
# Класс DatabaseTuningAdvisor наследуется от IndexTuningBase и реализует оптимизацию индексов
# для PostgreSQL, используя гибридный подход "seed + greedy". Он анализирует рабочую нагрузку
# запросов, генерирует кандидатов на индексы и выбирает оптимальную конфигурацию с учетом
# бюджета хранения, времени выполнения и других параметров.
class DatabaseTuningAdvisor(IndexTuningBase):
    def __init__(
        self,
        sql_driver: SqlDriver,
        budget_mb: int = -1,  # Без ограничения по умолчанию
        max_runtime_seconds: int = 30,  # 30 секунд
        max_index_width: int = 3,  # Максимальная ширина индекса
        min_column_usage: int = 1,  # Пропускать столбцы, используемые реже
        seed_columns_count: int = 3,  # Количество начальных одно-столбцовых индексов
        pareto_alpha: float = 2.0,  # Вес производительности в целевой функции
        min_time_improvement: float = 0.1,  # Минимальное улучшение времени
    ) -> None:
        """
        Описание метода __init__:
        Инициализирует DatabaseTuningAdvisor с настройками для оптимизации индексов.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для доступа к базе данных
            budget_mb (int): Бюджет хранения в МБ (-1 для отсутствия ограничения)
            max_runtime_seconds (int): Ограничение времени анализа в секундах
            max_index_width (int): Максимальное количество столбцов в индексе
            min_column_usage (int): Минимальное количество использований столбца
            seed_columns_count (int): Количество начальных одно-столбцовых индексов
            pareto_alpha (float): Вес производительности в целевой функции
            min_time_improvement (float): Минимальное улучшение времени выполнения

        Возвращает:
            None
        """
        super().__init__(sql_driver)
        self.budget_mb: int = budget_mb
        self.max_runtime_seconds: int = max_runtime_seconds
        self.max_index_width: int = max_index_width
        self.min_column_usage: int = min_column_usage
        self.seed_columns_count: int = seed_columns_count
        self._analysis_start_time: float = 0.0
        self.pareto_alpha: float = pareto_alpha
        self.min_time_improvement: float = min_time_improvement

    def _check_time(self) -> bool:
        """
        Описание метода _check_time:
        Проверяет, превышено ли максимальное время выполнения.

        Возвращает:
            bool: True, если время превышено
        """
        if self.max_runtime_seconds <= 0:
            return False
        elapsed: float = time.time() - self._analysis_start_time
        return elapsed > self.max_runtime_seconds

    @override
    async def _generate_recommendations(
        self, query_weights: List[Tuple[str, SelectStmt, float]]
    ) -> Tuple[Set[IndexRecommendation], float]:
        """
        Описание метода _generate_recommendations:
        Генерирует рекомендации по индексам, используя гибридный подход "seed + greedy"
        с ограничением по времени.

        Аргументы:
            query_weights (List[Tuple[str, SelectStmt, float]]): Список запросов с весами

        Возвращает:
            Tuple[Set[IndexRecommendation], float]: Множество рекомендаций и итоговую стоимость
        """
        existing_index_defs: Set[str] = {idx["definition"] for idx in await self._get_existing_indexes()}
        logger.debug(f"Существующие индексы ({len(existing_index_defs)}): {pp_list(list(existing_index_defs))}")

        all_candidates: List[IndexRecommendation] = await self.generate_candidates(query_weights, existing_index_defs)
        self.dta_trace(f"Все кандидаты ({len(all_candidates)}): {candidate_str(all_candidates)}")

        seeds_list: List[Set[IndexRecommendation]] = [set()]
        best_config: Tuple[Set[IndexRecommendation], float] = (set(), float("inf"))

        for seed in seeds_list:
            if self._check_time():
                break

            self.dta_trace("Оценка начального набора:")
            current_cost: float = await self._evaluate_configuration_cost(query_weights, frozenset(seed))
            candidate_indexes: Set[IndexRecommendation] = {
                IndexRecommendation(c.table, tuple(c.columns), c.using) for c in all_candidates
            }
            final_indexes, final_cost = await self._enumerate_greedy(
                query_weights, seed.copy(), current_cost, candidate_indexes - seed
            )

            if final_cost < best_config[1]:
                best_config = (final_indexes, final_cost)

        return best_config

    async def generate_candidates(
        self, workload: List[Tuple[str, SelectStmt, float]], existing_defs: Set[str]
    ) -> List[IndexRecommendation]:
        """
        Описание метода generate_candidates:
        Генерирует кандидатов на индексы из рабочей нагрузки запросов с пакетным созданием.

        Аргументы:
            workload (List[Tuple[str, SelectStmt, float]]): Список запросов с весами
            existing_defs (Set[str]): Множество определений существующих индексов

        Возвращает:
            List[IndexRecommendation]: Список отфильтрованных кандидатов
        """
        table_columns_usage: Dict[str, Dict[str, int]] = {}
        for _q, stmt, _ in workload:
            columns_per_table: Dict[str, Set[str]] = self._sql_bind_params.extract_stmt_columns(stmt)
            for tbl, cols in columns_per_table.items():
                if tbl not in table_columns_usage:
                    table_columns_usage[tbl] = {}
                for c in cols:
                    table_columns_usage[tbl][c] = table_columns_usage[tbl].get(c, 0) + 1

        table_columns: Dict[str, Set[str]] = {}
        for tbl, usage_map in table_columns_usage.items():
            kept_cols: Set[str] = {c for c, usage in usage_map.items() if usage >= self.min_column_usage}
            if kept_cols:
                table_columns[tbl] = kept_cols

        candidates: List[IndexRecommendation] = []
        for table, cols in table_columns.items():
            col_list: List[str] = list(cols)
            for width in range(1, min(self.max_index_width, len(cols)) + 1):
                for combo in combinations(col_list, width):
                    candidates.append(IndexRecommendation(table=table, columns=tuple(combo)))

        filtered_candidates: List[IndexRecommendation] = [
            c for c in candidates if not self._index_exists(c, existing_defs)
        ]
        condition_filtered1: List[IndexRecommendation] = self._filter_candidates_by_query_conditions(
            workload, filtered_candidates
        )
        condition_filtered: List[IndexRecommendation] = await self._filter_long_text_columns(condition_filtered1)

        self.dta_trace(f"Сгенерировано {len(candidates)} кандидатов")
        self.dta_trace(f"Отфильтровано до {len(filtered_candidates)} после удаления существующих индексов")
        self.dta_trace(f"Отфильтровано до {len(condition_filtered1)} после удаления неиспользуемых столбцов")
        self.dta_trace(f"Отфильтровано до {len(condition_filtered)} после удаления длинных текстовых столбцов")

        if len(condition_filtered) > 0:
            query: str = "SELECT hypopg_create_index({});" * len(condition_filtered)
            await SafeSqlDriver.execute_param_query(
                self.sql_driver, query, [idx.definition for idx in condition_filtered]
            )

            result = await self.sql_driver.execute_query(
                "SELECT index_name, hypopg_relation_size(indexrelid) as index_size FROM hypopg_list_indexes;"
            )
            if result is not None:
                index_map: Dict[str, Any] = {r.cells["index_name"]: r.cells["index_size"] for r in result}
                for idx in condition_filtered:
                    if idx.name in index_map:
                        idx.estimated_size_bytes = index_map[idx.name]

            await self.sql_driver.execute_query("SELECT hypopg_reset();")

        return condition_filtered

    async def _enumerate_greedy(
        self,
        queries: List[Tuple[str, SelectStmt, float]],
        current_indexes: Set[IndexRecommendation],
        current_cost: float,
        candidate_indexes: Set[IndexRecommendation],
    ) -> Tuple[Set[IndexRecommendation], float]:
        """
        Описание метода _enumerate_greedy:
        Реализует жадный алгоритм для выбора оптимальных индексов с учетом Парето-оптимизации.

        Аргументы:
            queries (List[Tuple[str, SelectStmt, float]]): Список запросов с весами
            current_indexes (Set[IndexRecommendation]): Текущий набор индексов
            current_cost (float): Текущая стоимость (время выполнения)
            candidate_indexes (Set[IndexRecommendation]): Кандидаты на добавление

        Возвращает:
            Tuple[Set[IndexRecommendation], float]: Итоговый набор индексов и стоимость
        """
        import math

        alpha: float = self.pareto_alpha
        min_time_improvement: float = self.min_time_improvement

        self.dta_trace("\n[ЖАДНЫЙ ПОИСК] Начало перечисления")
        self.dta_trace(f"  - Параметры: alpha={alpha}, min_time_improvement={min_time_improvement}")
        self.dta_trace(f"  - Начальные индексы: {len(current_indexes)}, Кандидаты: {len(candidate_indexes)}")

        tables: Set[str] = {idx.table for idx in candidate_indexes}
        base_relation_size: int = sum([await self._get_table_size(table) for table in tables])
        self.dta_trace(f"  - Размер базовых отношений: {humanize.naturalsize(base_relation_size)}")

        indexes_size: int = sum([await self._estimate_index_size(idx.table, list(idx.columns)) for idx in current_indexes])
        current_space: int = base_relation_size + indexes_size
        current_time: float = current_cost
        current_objective: float = (
            math.log(current_time) + alpha * math.log(current_space)
            if current_cost > 0 and current_space > 0
            else float("inf")
        )

        self.dta_trace(
            f"  - Начальная конфигурация: Время={current_time:.2f}, "
            f"Пространство={humanize.naturalsize(current_space)} (База: {humanize.naturalsize(base_relation_size)}, "
            f"Индексы: {humanize.naturalsize(indexes_size)}), "
            f"Целевая функция={current_objective:.4f}"
        )

        added_indexes: List[IndexRecommendation] = []
        iteration: int = 1

        while True:
            self.dta_trace(f"\n[ИТЕРАЦИЯ {iteration}] Оценка кандидатов")
            best_index: Optional[IndexRecommendation] = None
            best_time: float = current_time
            best_space: int = current_space
            best_objective: float = current_objective
            best_time_improvement: float = 0

            for candidate in candidate_indexes:
                self.dta_trace(f"Оценка кандидата: {candidate_str([candidate])}")
                index_size: int = await self._estimate_index_size(candidate.table, list(candidate.columns))
                self.dta_trace(f"    + Размер индекса: {humanize.naturalsize(index_size)}")
                test_space: int = current_space + index_size
                self.dta_trace(f"    + Общее пространство: {humanize.naturalsize(test_space)}")

                if self.budget_mb > 0 and (test_space - base_relation_size) > self.budget_mb * 1024 * 1024:
                    self.dta_trace(
                        f"  - Пропуск кандидата: {candidate_str([candidate])}, так как размер индексов "
                        f"({humanize.naturalsize(test_space - base_relation_size)}) превышает бюджет "
                        f"({humanize.naturalsize(self.budget_mb * 1024 * 1024)})"
                    )
                    continue

                test_time: float = await self._evaluate_configuration_cost(
                    queries, frozenset(idx.index_definition for idx in current_indexes | {candidate})
                )
                self.dta_trace(f"    + Стоимость оценки (время): {test_time}")

                time_improvement: float = (current_time - test_time) / current_time
                if time_improvement < min_time_improvement:
                    self.dta_trace(
                        f"  - Пропуск кандидата: {candidate_str([candidate])}, так как улучшение времени ниже порога"
                    )
                    continue

                test_objective: float = math.log(test_time) + alpha * math.log(test_space)
                if test_objective < best_objective and time_improvement > best_time_improvement:
                    self.dta_trace(f"  - Обновление лучшего кандидата: {candidate_str([candidate])}")
                    best_index = candidate
                    best_time = test_time
                    best_space = test_space
                    best_objective = test_objective
                    best_time_improvement = time_improvement
                else:
                    self.dta_trace(
                        f"  - Пропуск кандидата: {candidate_str([candidate])}, так как улучшение целевой функции недостаточно"
                    )

            if best_index is None:
                self.dta_trace(f"ПОИСК ОСТАНОВЛЕН: Не найдены индексы с улучшением времени >= {min_time_improvement:.2%}")
                break

            time_improvement = (current_time - best_time) / current_time
            space_increase: float = (best_space - current_space) / current_space
            objective_improvement: float = current_objective - best_objective

            self.dta_trace(
                f"  - Выбранный индекс: {candidate_str([best_index])}"
                f"\n    + Улучшение времени: {time_improvement:.2%}"
                f"\n    + Увеличение пространства: {space_increase:.2%}"
                f"\n    + Новая целевая функция: {best_objective:.4f} (улучшение: {objective_improvement:.4f})"
            )

            current_indexes.add(best_index)
            candidate_indexes.remove(best_index)
            added_indexes.append(best_index)

            current_time = best_time
            current_space = best_space
            current_objective = best_objective

            iteration += 1

            if self._check_time():
                self.dta_trace("ПОИСК ОСТАНОВЛЕН: Достигнут предел времени")
                break

        self.dta_trace("\n[ПОИСК ЗАВЕРШЕН]")
        if added_indexes:
            indexes_size = sum([await self._estimate_index_size(idx.table, list(idx.columns)) for idx in current_indexes])
            self.dta_trace(
                f"  - Итоговая конфигурация: добавлено {len(added_indexes)} индексов"
                f"\n    + Итоговое время: {current_time:.2f}"
                f"\n    + Итоговое пространство: {humanize.naturalsize(current_space)} (База: {humanize.naturalsize(base_relation_size)}, "
                f"Индексы: {humanize.naturalsize(indexes_size)})"
                f"\n    + Итоговая целевая функция: {current_objective:.4f}"
            )
        else:
            self.dta_trace("Индексы не добавлены - базовая конфигурация оптимальна")

        return current_indexes, current_time

    def _filter_candidates_by_query_conditions(
        self, workload: List[Tuple[str, SelectStmt, float]], candidates: List[IndexRecommendation]
    ) -> List[IndexRecommendation]:
        """
        Описание метода _filter_candidates_by_query_conditions:
        Фильтрует кандидатов на индексы, оставляя только те, чьи столбцы используются в условиях запросов.

        Аргументы:
            workload (List[Tuple[str, SelectStmt, float]]): Список запросов с весами
            candidates (List[IndexRecommendation]): Список кандидатов

        Возвращает:
            List[IndexRecommendation]: Отфильтрованный список кандидатов
        """
        if not workload or not candidates:
            return candidates

        condition_columns: Dict[str, Set[str]] = {}
        for _, stmt, _ in workload:
            try:
                collector = ConditionColumnCollector()
                collector(stmt)
                query_condition_columns: Dict[str, Set[str]] = collector.condition_columns
                for table, cols in query_condition_columns.items():
                    if table not in condition_columns:
                        condition_columns[table] = set()
                    condition_columns[table].update(cols)
            except Exception as e:
                raise ValueError("Ошибка при извлечении столбцов условий из запроса") from e

        filtered_candidates: List[IndexRecommendation] = []
        for candidate in candidates:
            table: str = candidate.table
            if table not in condition_columns:
                continue
            all_columns_used: bool = all(col in condition_columns[table] for col in candidate.columns)
            if all_columns_used:
                filtered_candidates.append(candidate)

        return filtered_candidates

    async def _filter_long_text_columns(
        self, candidates: List[IndexRecommendation], max_text_length: int = 100
    ) -> List[IndexRecommendation]:
        """
        Описание метода _filter_long_text_columns:
        Фильтрует индексы, содержащие длинные текстовые столбцы, на основе каталога.

        Аргументы:
            candidates (List[IndexRecommendation]): Список кандидатов
            max_text_length (int): Максимальная длина текста (по умолчанию 100)

        Возвращает:
            List[IndexRecommendation]: Отфильтрованный список кандидатов
        """
        if not candidates:
            return []

        table_columns: Set[Tuple[str, str]] = set()
        for candidate in candidates:
            for column in candidate.columns:
                table_columns.add((candidate.table, column))

        tables_array: str = ",".join(f"'{table}'" for table, _ in table_columns)
        columns_array: str = ",".join(f"'{col}'" for _, col in table_columns)

        type_query: str = f"""
            SELECT
                c.table_name,
                c.column_name,
                c.data_type,
                c.character_maximum_length,
                pg_stats.avg_width,
                CASE
                    WHEN c.data_type = 'text' THEN true
                    WHEN (c.data_type = 'character varying' OR c.data_type = 'varchar' OR
                         c.data_type = 'character' OR c.data_type = 'char') AND
                         (c.character_maximum_length IS NULL OR c.character_maximum_length > {max_text_length})
                    THEN true
                    ELSE false
                END as potential_long_text
            FROM information_schema.columns c
            LEFT JOIN pg_stats ON
                pg_stats.tablename = c.table_name AND
                pg_stats.attname = c.column_name
            WHERE c.table_name IN ({tables_array})
            AND c.column_name IN ({columns_array})
        """

        result: Optional[List[SqlDriver.RowResult]] = await self.sql_driver.execute_query(type_query)
        logger.debug(f"Типы столбцов и ограничения длины: {result}")

        if not result:
            logger.debug("Типы столбцов и ограничения длины не найдены")
            return []

        problematic_columns: Set[Tuple[str, str]] = set()
        potential_problematic_columns: Set[Tuple[str, str]] = set()

        for row in result:
            table: str = row.cells["table_name"]
            column: str = row.cells["column_name"]
            potential_long: bool = row.cells["potential_long_text"]
            avg_width: Optional[int] = row.cells.get("avg_width")

            if potential_long and (avg_width is None or avg_width > max_text_length * 0.4):
                problematic_columns.add((table, column))
                logger.debug(f"Обнаружен потенциально длинный текстовый столбец: {table}.{column} (avg_width: {avg_width})")
            elif potential_long:
                potential_problematic_columns.add((table, column))

        filtered_candidates: List[IndexRecommendation] = []
        for candidate in candidates:
            valid: bool = True
            for column in candidate.columns:
                if (candidate.table, column) in problematic_columns:
                    valid = False
                    logger.debug(f"Пропуск кандидата индекса с длинным текстовым столбцом: {candidate.table}.{column}")
                    break
                elif (candidate.table, column) in potential_problematic_columns:
                    candidate.potential_problematic_reason = "long_text_column"
            if valid:
                filtered_candidates.append(candidate)

        return filtered_candidates

    async def _get_existing_indexes(self) -> List[Dict[str, Any]]:
        """
        Описание метода _get_existing_indexes:
        Получает список существующих индексов из базы данных.

        Возвращает:
            List[Dict[str, Any]]: Список словарей с информацией об индексах
        """
        query: str = """
        SELECT schemaname as schema,
               tablename as table,
               indexname as name,
               indexdef as definition
        FROM pg_indexes
        WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
        ORDER BY schemaname, tablename, indexname
        """
        result: Optional[List[SqlDriver.RowResult]] = await self.sql_driver.execute_query(query)
        if result is not None:
            return [dict(row.cells) for row in result]
        return []

    def _index_exists(self, index: IndexRecommendation, existing_defs: Set[str]) -> bool:
        """
        Описание метода _index_exists:
        Проверяет, существует ли индекс с указанными таблицей, столбцами и типом.

        Аргументы:
            index (IndexRecommendation): Кандидат на индекс
            existing_defs (Set[str]): Множество определений существующих индексов

        Возвращает:
            bool: True, если индекс существует
        """
        from pglast import parser

        try:
            candidate_stmt = parser.parse_sql(index.definition)[0]
            candidate_node = candidate_stmt.stmt
            candidate_info: Optional[Dict[str, Any]] = self._extract_index_info(candidate_node)

            if not candidate_info:
                return index.definition in existing_defs

            for existing_def in existing_defs:
                if not ("CREATE INDEX" in existing_def.upper() or "CREATE UNIQUE INDEX" in existing_def.upper()):
                    continue

                try:
                    existing_stmt = parser.parse_sql(existing_def)[0]
                    existing_node = existing_stmt.stmt
                    existing_info: Optional[Dict[str, Any]] = self._extract_index_info(existing_node)

                    if existing_info and self._is_same_index(candidate_info, existing_info):
                        return True
                except Exception as e:
                    raise ValueError("Ошибка при разборе существующего индекса") from e

            return False
        except Exception as e:
            raise ValueError("Ошибка при сравнении индексов") from e

    def _extract_index_info(self, node: Any) -> Optional[Dict[str, Any]]:
        """
        Описание метода _extract_index_info:
        Извлекает ключевую информацию из узла индекса AST.

        Аргументы:
            node (Any): Узел AST индекса

        Возвращает:
            Optional[Dict[str, Any]]: Информация об индексе или None
        """
        try:
            index_stmt = node.IndexStmt if hasattr(node, "IndexStmt") else node
            table_name: str = (
                index_stmt.relation.relname
                if hasattr(index_stmt.relation, "relname")
                else index_stmt.relation.RangeVar.relname
            )

            columns: List[str] = []
            for idx_elem in index_stmt.indexParams:
                if hasattr(idx_elem, "name") and idx_elem.name:
                    columns.append(idx_elem.name)
                elif hasattr(idx_elem, "IndexElem") and idx_elem.IndexElem:
                    columns.append(idx_elem.IndexElem.name)
                elif hasattr(idx_elem, "expr") and idx_elem.expr:
                    expr_str: str = self._ast_expr_to_string(idx_elem.expr)
                    columns.append(expr_str)

            index_type: str = index_stmt.accessMethod if hasattr(index_stmt, "accessMethod") and index_stmt.accessMethod else "btree"
            is_unique: bool = index_stmt.unique if hasattr(index_stmt, "unique") else False

            return {
                "table": table_name.lower(),
                "columns": [col.lower() for col in columns],
                "type": index_type.lower(),
                "unique": is_unique,
            }
        except Exception as e:
            self.dta_trace(f"Ошибка при извлечении информации об индексе: {e}")
            raise ValueError("Ошибка при извлечении информации об индексе") from e

    def _ast_expr_to_string(self, expr: Any) -> str:
        """
        Описание метода _ast_expr_to_string:
        Преобразует выражение AST (например, FuncCall) в строковое представление.

        Аргументы:
            expr (Any): Узел выражения AST

        Возвращает:
            str: Строковое представление выражения
        """
        try:
            from pglast.ast import ColumnRef, FuncCall

            if isinstance(expr, FuncCall):
                func_name: str = ".".join([name.sval for name in expr.funcname if hasattr(name, "sval")])
                args: List[str] = [self._ast_expr_to_string(arg) for arg in expr.args] if hasattr(expr, "args") and expr.args else []
                return f"{func_name}({','.join(args)})"

            elif isinstance(expr, ColumnRef):
                return ".".join([field.sval for field in expr.fields if hasattr(field, "sval")]) if hasattr(expr, "fields") and expr.fields else "unknown_column"

            elif hasattr(expr, "sval"):
                return expr.sval
            elif hasattr(expr, "ival"):
                return str(expr.ival)
            elif hasattr(expr, "fval"):
                return expr.fval

            return str(expr)
        except Exception as e:
            raise ValueError("Ошибка при преобразовании выражения в строку") from e

    def _is_same_index(self, index1: Dict[str, Any], index2: Dict[str, Any]) -> bool:
        """
        Описание метода _is_same_index:
        Проверяет, являются ли два индекса функционально эквивалентными.

        Аргументы:
            index1 (Dict[str, Any]): Информация о первом индексе
            index2 (Dict[str, Any]): Информация о втором индексе

        Возвращает:
            bool: True, если индексы эквивалентны
        """
        if not index1 or not index2:
            return False

        if index1["table"] != index2["table"] or index1["type"] != index2["type"]:
            return False

        if index1["columns"] != index2["columns"]:
            if index1["type"] == "hash" and set(index1["columns"]) == set(index2["columns"]):
                return True
            return False

        if index1["unique"] and not index2["unique"]:
            return False

        return True

# Описание класса ConditionColumnCollector
#
# Класс ConditionColumnCollector наследуется от ColumnCollector и собирает только столбцы,
# используемые в условиях WHERE, JOIN, HAVING, с учетом псевдонимов таблиц и столбцов.
class ConditionColumnCollector(ColumnCollector):
    """
    Специализированная версия ColumnCollector, собирающая только столбцы,
    используемые в условиях WHERE, JOIN, HAVING, с учетом псевдонимов.
    """

    def __init__(self) -> None:
        """
        Описание метода __init__:
        Инициализирует ConditionColumnCollector.

        Возвращает:
            None
        """
        super().__init__()
        self.condition_columns: Dict[str, Set[str]] = {}
        self.in_condition: bool = False

    def __call__(self, node: Node) -> Dict[str, Set[str]]:
        """
        Описание метода __call__:
        Вызывает обработку узла и возвращает собранные столбцы условий.

        Аргументы:
            node (Node): Узел AST для обработки

        Возвращает:
            Dict[str, Set[str]]: Словарь таблица -> множество столбцов
        """
        super().__call__(node)
        return self.condition_columns

    def visit_SelectStmt(self, ancestors: List[Node], node: Node) -> None:
        """
        Описание метода visit_SelectStmt:
        Обрабатывает узел SelectStmt, фокусируясь на условиях запроса.

        Аргументы:
            ancestors (List[Node]): Список родительских узлов
            node (Node): Узел SelectStmt

        Возвращает:
            None
        """
        if isinstance(node, SelectStmt):
            self.inside_select = True
            self.current_query_level += 1
            query_level: int = self.current_query_level

            alias_visitor = TableAliasVisitor()
            if hasattr(node, "fromClause") and node.fromClause:
                for from_item in node.fromClause:
                    alias_visitor(from_item)
            tables: Set[str] = alias_visitor.tables
            aliases: Dict[str, str] = alias_visitor.aliases

            self.context_stack.append((tables, aliases))

            if hasattr(node, "targetList") and node.targetList:
                self.target_list = node.targetList
                for target_entry in self.target_list:
                    if hasattr(target_entry, "name") and target_entry.name:
                        col_alias: str = target_entry.name
                        if hasattr(target_entry, "val"):
                            self.column_aliases[col_alias] = {
                                "node": target_entry.val,
                                "level": query_level,
                            }

            if node.whereClause:
                in_condition_cache: bool = self.in_condition
                self.in_condition = True
                self(node.whereClause)
                self.in_condition = in_condition_cache

            if node.fromClause:
                for item in node.fromClause:
                    if isinstance(item, JoinExpr) and item.quals:
                        in_condition_cache = self.in_condition
                        self.in_condition = True
                        self(item.quals)
                        self.in_condition = in_condition_cache

            if node.havingClause:
                in_condition_cache = self.in_condition
                self.in_condition = True
                self._process_having_with_aliases(node.havingClause)
                self.in_condition = in_condition_cache

            if hasattr(node, "sortClause") and node.sortClause:
                in_condition_cache = self.in_condition
                self.in_condition = True
                for sort_item in node.sortClause:
                    self._process_node_with_aliases(sort_item.node)
                self.in_condition = in_condition_cache

            self.context_stack.pop()
            self.inside_select = False
            self.current_query_level -= 1

    def _process_having_with_aliases(self, having_clause: Any) -> None:
        """
        Описание метода _process_having_with_aliases:
        Обрабатывает HAVING-условие с учетом псевдонимов столбцов.

        Аргументы:
            having_clause (Any): Узел HAVING-условия

        Возвращает:
            None
        """
        self._process_node_with_aliases(having_clause)

    def _process_node_with_aliases(self, node: Any) -> None:
        """
        Описание метода _process_node_with_aliases:
        Обрабатывает узел, разрешая псевдонимы столбцов.

        Аргументы:
            node (Any): Узел для обработки

        Возвращает:
            None
        """
        if node is None:
            return

        if isinstance(node, ColumnRef) and hasattr(node, "fields") and node.fields:
            fields: List[str] = [f.sval for f in node.fields if hasattr(f, "sval")]
            if len(fields) == 1:
                col_name: str = fields[0]
                if col_name in self.column_aliases:
                    alias_info: Dict[str, Any] = self.column_aliases[col_name]
                    if alias_info["level"] == self.current_query_level:
                        self(alias_info["node"])
                        return

        self(node)

    def visit_ColumnRef(self, ancestors: List[Node], node: Node) -> None:
        """
        Описание метода visit_ColumnRef:
        Обрабатывает ссылки на столбцы в контексте условий.

        Аргументы:
            ancestors (List[Node]): Список родительских узлов
            node (Node): Узел ColumnRef

        Возвращает:
            None
        """
        if not self.in_condition or not isinstance(node, ColumnRef) or not self.context_stack:
            return

        tables, aliases = self.context_stack[-1]
        fields: List[str] = [f.sval for f in node.fields if hasattr(f, "sval")] if node.fields else []

        if len(fields) == 1 and fields[0] in self.column_aliases:
            alias_info: Dict[str, Any] = self.column_aliases[fields[0]]
            if alias_info["level"] == self.current_query_level:
                self.in_condition = True
                self(alias_info["node"])
                return

        if len(fields) == 2:
            table_or_alias, column = fields
            table: str = aliases.get(table_or_alias, table_or_alias)
            if table not in self.condition_columns:
                self.condition_columns[table] = set()
            self.condition_columns[table].add(column)

        elif len(fields) == 1:
            column: str = fields[0]
            found_match: bool = False
            for table in tables:
                if "." in table:
                    _, table = table.split(".", 1)
                if self._column_exists(table, column):
                    if table not in self.condition_columns:
                        self.condition_columns[table] = set()
                    self.condition_columns[table].add(column)
                    found_match = True
            if not found_match:
                logger.debug(f"Не удалось разрешить неквалифицированный столбец '{column}' для таблицы")

    def _column_exists(self, table: str, column: str) -> bool:
        """
        Описание метода _column_exists:
        Проверяет существование столбца в таблице.

        Аргументы:
            table (str): Имя таблицы
            column (str): Имя столбца

        Возвращает:
            bool: True, если столбец существует
        """
        # TODO: Реализовать запрос к базе данных для проверки
        return True