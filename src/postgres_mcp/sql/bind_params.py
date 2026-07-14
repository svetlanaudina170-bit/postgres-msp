# Анализ файла bind_params.py
#
# Описание файла:
# Файл bind_params.py содержит утилиты для обработки и замены параметров в SQL запросах.
# Он использует библиотеку pglast для парсинга SQL и предоставляет классы для извлечения информации о таблицах,
# псевдонимах и столбцах, а также для замены параметризованных заполнителей (например, $1, $2) реальными значениями
# на основе статистики столбцов из PostgreSQL. Это полезно для анализа запросов из pg_stat_statements.
#
# Используемые модули:
# - logging: для логирования событий
# - re: для работы с регулярными выражениями
# - typing: для аннотаций типов
# - pglast: для парсинга SQL запросов и работы с AST (абстрактным синтаксическим деревом)
#
# Импорты:
# - safe_sql: SafeSqlDriver для безопасного выполнения SQL запросов
# - sql_driver: SqlDriver для выполнения SQL запросов
#
# Основные компоненты:
# - Класс TableAliasVisitor: извлекает псевдонимы и имена таблиц из SQL AST
# - Класс ColumnCollector: собирает столбцы, используемые в различных частях запроса
# - Класс SqlBindParams: заменяет параметризованные заполнители значениями на основе статистики
#
# Зависимости:
# Файл связан с модулями safe_sql и sql_driver, а также используется в контексте пакета,
# взаимодействующего с PostgreSQL, например, в server.py и top_queries_calc.py.

import logging
import re
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union

from pglast import parse_sql
from pglast.ast import A_Expr
from pglast.ast import ColumnRef
from pglast.ast import JoinExpr
from pglast.ast import Node
from pglast.ast import RangeVar
from pglast.ast import SelectStmt
from pglast.ast import SortBy
from pglast.ast import SortGroupClause
from pglast.visitors import Visitor

from .safe_sql import SafeSqlDriver
from .sql_driver import SqlDriver

# Инициализация логгера
logger = logging.getLogger(__name__)

# --- Классы для обработки AST ---


# Описание класса TableAliasVisitor
#
# Класс TableAliasVisitor извлекает псевдонимы и имена таблиц из SQL AST.
# Используется для определения соответствия между псевдонимами и реальными именами таблиц.
class TableAliasVisitor(Visitor):
    """Извлекает псевдонимы и имена таблиц из SQL AST."""

    aliases: Dict[str, str]  # Словарь псевдонимов (ключ: псевдоним, значение: имя таблицы)
    tables: Set[str]  # Множество имен таблиц

    def __init__(self) -> None:
        """
        Описание метода __init__:
        Инициализирует объект TableAliasVisitor.

        Возвращает:
            None
        """
        super().__init__()
        self.aliases = {}
        self.tables = set()

    def __call__(self, node: Node) -> Tuple[Dict[str, str], Set[str]]:
        """
        Описание метода __call__:
        Вызывает visitor для обработки узла AST и возвращает собранные псевдонимы и таблицы.

        Аргументы:
            node (Node): Узел AST для обработки

        Возвращает:
            Tuple[Dict[str, str], Set[str]]: Кортеж из словаря псевдонимов и множества таблиц
        """
        super().__call__(node)
        return self.aliases, self.tables

    def visit_RangeVar(self, ancestors: List[Node], node: Node) -> None:
        """
        Описание метода visit_RangeVar:
        Обрабатывает узел RangeVar, представляющий ссылку на таблицу в FROM clause.

        Аргументы:
            ancestors (List[Node]): Список родительских узлов
            node (Node): Текущий узел AST

        Возвращает:
            None
        """
        if isinstance(node, RangeVar):
            # Добавление имени таблицы
            if node.relname is not None:
                self.tables.add(node.relname)
            # Добавление псевдонима
            if node.alias and hasattr(node.alias, "aliasname") and node.alias.aliasname is not None:
                self.aliases[node.alias.aliasname] = node.relname

    def visit_JoinExpr(self, ancestors: List[Node], node: Node) -> None:
        """
        Описание метода visit_JoinExpr:
        Обрабатывает узел JoinExpr, представляющий JOIN-выражение, рекурсивно обрабатывая оба операнда.

        Аргументы:
            ancestors (List[Node]): Список родительских узлов
            node (Node): Текущий узел AST

        Возвращает:
            None
        """
        if isinstance(node, JoinExpr):
            # Обработка левого операнда
            if node.larg is not None:
                self(node.larg)
            # Обработка правого операнда
            if node.rarg is not None:
                self(node.rarg)


# Описание класса ColumnCollector
#
# Класс ColumnCollector собирает столбцы, используемые в WHERE, JOIN, ORDER BY, GROUP BY, HAVING и SELECT.
# Учитывает псевдонимы столбцов и обрабатывает вложенные запросы.
class ColumnCollector(Visitor):
    """
    Собирает столбцы, используемые в различных частях SQL запроса (WHERE, JOIN, ORDER BY, GROUP BY, HAVING, SELECT).
    Улучшена обработка псевдонимов столбцов.
    """

    context_stack: List[Tuple[Set[str], str]]  # Стек контекстов (таблицы, псевдонимы) для каждого уровня
    columns: Dict[str, Set[str]]  # Собранные столбцы, ключ — имя таблицы
    target_list: Optional[List[Node]]  # Список целевых выражений (SELECT clause)
    inside_select: bool  # Флаг нахождения внутри SELECT
    column_aliases: Dict[str, Dict[str, Any]]  # Псевдонимы столбцов и их определения
    current_query_level: int  # Уровень вложенности запроса

    def __init__(self) -> None:
        """
        Описание метода __init__:
        Инициализирует объект ColumnCollector.

        Возвращает:
            None
        """
        super().__init__()
        self.context_stack = []
        self.columns = {}
        self.target_list = None
        self.inside_select = False
        self.column_aliases = {}
        self.current_query_level = 0

    def __call__(self, node: Node) -> Dict[str, Set[str]]:
        """
        Описание метода __call__:
        Вызывает visitor для обработки узла и возвращает собранные столбцы.

        Аргументы:
            node (Node): Узел AST для обработки

        Возвращает:
            Dict[str, Set[str]]: Словарь столбцов, сгруппированных по таблицам
        """
        super().__call__(node)
        return self.columns

    def visit_SelectStmt(self, ancestors: List[Node], node: Node) -> None:
        """
        Описание метода visit_SelectStmt:
        Обрабатывает узел SelectStmt, собирая псевдонимы таблиц и столбцов.

        Аргументы:
            ancestors (List[Node]): Список родительских узлов
            node (Node): Текущий узел AST

        Возвращает:
            None
        """
        if isinstance(node, SelectStmt):
            self.inside_select = True
            self.current_query_level += 1
            query_level: int = self.current_query_level

            # Сбор таблиц и псевдонимов
            alias_visitor = TableAliasVisitor()
            if node.fromClause:
                for from_item in node.fromClause:
                    alias_visitor(from_item)
            scope_tables: Set[str] = alias_visitor.tables
            scope_aliases: Dict[str, str] = alias_visitor.aliases

            # Добавление контекста
            self.context_stack.append((scope_tables, scope_aliases))

            # Первый проход: сбор псевдонимов столбцов из targetList
            if node.targetList:
                self.target_list = node.targetList
                for target_entry in self.target_list:
                    if hasattr(target_entry, "name") and target_entry.name:
                        col_alias: str = target_entry.name
                        if hasattr(target_entry, "val"):
                            self.column_aliases[col_alias] = {
                                "node": target_entry.val,
                                "level": query_level,
                            }

            # Второй проход: обработка остальных частей запроса
            self._process_query_clauses(node)

            # Удаление контекста
            self.context_stack.pop()
            self.inside_select = False
            self.current_query_level -= 1

    def _process_query_clauses(self, node: SelectStmt) -> None:
        """
        Описание метода _process_query_clauses:
        Обрабатывает различные части запроса для сбора столбцов.

        Аргументы:
            node (SelectStmt): Узел SELECT запроса

        Возвращает:
            None
        """
        # Обработка targetList
        if node.targetList:
            self.target_list = node.targetList
            for target_entry in self.target_list:
                if hasattr(target_entry, "val"):
                    self(target_entry.val)

        # Обработка GROUP BY
        if node.groupClause:
            for group_item in node.groupClause:
                if isinstance(group_item, SortGroupClause) and isinstance(group_item.tleSortGroupRef, int):
                    ref_index: int = group_item.tleSortGroupRef
                    if self.target_list and ref_index <= len(self.target_list):
                        target_entry = self.target_list[ref_index - 1]
                        if hasattr(target_entry, "val"):
                            self(target_entry.val)
                        if hasattr(target_entry, "expr"):
                            self(target_entry.expr)

        # Обработка WHERE
        if node.whereClause:
            self(node.whereClause)

        # Обработка FROM
        if node.fromClause:
            for from_item in node.fromClause:
                self(from_item)

        # Обработка HAVING
        if node.havingClause:
            self(node.havingClause)

        # Обработка ORDER BY
        if node.sortClause:
            for sort_item in node.sortClause:
                self._process_sort_item(sort_item)

    def _process_sort_item(self, sort_item: SortBy) -> None:
        """
        Описание метода _process_sort_item:
        Обрабатывает элемент ORDER BY, разрешая псевдонимы столбцов.

        Аргументы:
            sort_item (SortBy): Узел сортировки

        Возвращает:
            None
        """
        if not hasattr(sort_item, "node"):
            return

        # Проверка на псевдоним столбца
        if isinstance(sort_item.node, ColumnRef) and sort_item.node.fields:
            fields: List[str] = [f.sval for f in sort_item.node.fields if hasattr(f, "sval")]
            if len(fields) == 1:
                col_name: str = fields[0]
                if col_name in self.column_aliases:
                    alias_info = self.column_aliases[col_name]
                    if alias_info["level"] == self.current_query_level:
                        self(alias_info["node"])
                        return

        # Обычная обработка
        self(sort_item.node)

    def visit_ColumnRef(self, ancestors: List[Node], node: Node) -> None:
        """
        Описание метода visit_ColumnRef:
        Обрабатывает узел ColumnRef, собирая имена столбцов, пропуская псевдонимы.

        Аргументы:
            ancestors (List[Node]): Список родительских узлов
            node (Node): Текущий узел AST

        Возвращает:
            None
        """
        if isinstance(node, ColumnRef) and self.inside_select:
            if not node.fields:
                return

            fields: List[str] = [f.sval if hasattr(f, "sval") else "*" for f in node.fields]

            # Пропуск псевдонимов и звездочек
            if len(fields) == 1 and (fields[0] == "*" or fields[0] in self.column_aliases):
                return
            if len(fields) == 2 and fields[1] == "*":
                return

            # Получение текущего контекста
            current_tables, current_aliases = self.context_stack[-1] if self.context_stack else (set(), {})

            if len(fields) == 2:  # Квалифицированный столбец (например, u.name)
                table_or_alias, column = fields
                table: str = current_aliases.get(table_or_alias, table_or_alias)
                if table not in self.columns:
                    self.columns[table] = set()
                self.columns[table].add(column)
            elif len(fields) == 1:  # Неквалифицированный столбец
                column: str = fields[0]
                if len(current_tables) == 1:
                    table = next(iter(current_tables))
                    if table not in self.columns:
                        self.columns[table] = set()
                    self.columns[table].add(column)
                else:
                    for table in current_tables:
                        if self._column_exists(table, column):
                            if table not in self.columns:
                                self.columns[table] = set()
                            self.columns[table].add(column)
                            break

    def _column_exists(self, table: str, column: str) -> bool:
        """
        Описание метода _column_exists:
        Проверяет существование столбца в таблице (заглушка).

        Аргументы:
            table (str): Имя таблицы
            column (str): Имя столбца

        Возвращает:
            bool: True (заглушка, предполагает существование столбца)
        """
        # Заглушка, в реальной реализации требуется запрос к схеме
        return True

    def visit_A_Expr(self, ancestors: List[Node], node: Node) -> None:
        """
        Описание метода visit_A_Expr:
        Обрабатывает узел A_Expr (арифметическое или сравнительное выражение).

        Аргументы:
            ancestors (List[Node]): Список родительских узлов
            node (Node): Текущий узел AST

        Возвращает:
            None
        """
        if isinstance(node, A_Expr) and self.inside_select:
            # Обработка левого выражения
            if node.lexpr:
                self(node.lexpr)
                if isinstance(node.lexpr, SelectStmt):
                    alias_visitor = TableAliasVisitor()
                    alias_visitor(node.lexpr)
                    self.context_stack.append((alias_visitor.tables, alias_visitor.aliases))
                    self(node.lexpr)
                    self.context_stack.pop()

            # Обработка правого выражения
            if node.rexpr:
                if isinstance(node.rexpr, SelectStmt):
                    alias_visitor = TableAliasVisitor()
                    alias_visitor(node.rexpr)
                    self.context_stack.append((alias_visitor.tables, alias_visitor.aliases))
                    self(node.rexpr)
                    self.context_stack.pop()
                else:
                    self(node.rexpr)

            # Специальная обработка IN
            if node.kind == 0 and node.rexpr and isinstance(node.rexpr, SelectStmt):
                alias_visitor = TableAliasVisitor()
                alias_visitor(node.rexpr)
                self.context_stack.append((alias_visitor.tables, alias_visitor.aliases))
                self(node.rexpr)
                self.context_stack.pop()

    def visit_JoinExpr(self, ancestors: List[Node], node: Node) -> None:
        """
        Описание метода visit_JoinExpr:
        Обрабатывает узел JoinExpr для условий JOIN.

        Аргументы:
            ancestors (List[Node]): Список родительских узлов
            node (Node): Текущий узел AST

        Возвращает:
            None
        """
        if isinstance(node, JoinExpr) and self.inside_select:
            if node.larg:
                self(node.larg)
            if node.rarg:
                self(node.rarg)
            if node.quals:
                self(node.quals)

    def visit_SortBy(self, ancestors: List[Node], node: Node) -> None:
        """
        Описание метода visit_SortBy:
        Обрабатывает узел SortBy (ORDER BY выражение).

        Аргументы:
            ancestors (List[Node]): Список родительских узлов
            node (Node): Текущий узел AST

        Возвращает:
            None
        """
        if isinstance(node, SortBy) and self.inside_select:
            if node.node:
                self(node.node)


# Описание класса SqlBindParams
#
# Класс SqlBindParams заменяет параметризованные заполнители ($1, $2) в запросах
# реальными значениями на основе статистики столбцов из pg_stats.
class SqlBindParams:
    """
    Заменяет параметризованные заполнители значениями на основе статистики столбцов.
    """

    sql_driver: SqlDriver  # SQL драйвер для выполнения запросов
    _column_stats_cache: Dict[str, Optional[Dict[str, Any]]]  # Кэш статистики столбцов

    def __init__(self, sql_driver: SqlDriver) -> None:
        """
        Описание метода __init__:
        Инициализирует объект SqlBindParams с указанным SQL драйвером.

        Аргументы:
            sql_driver (SqlDriver): SQL драйвер для взаимодействия с базой данных

        Возвращает:
            None
        """
        self.sql_driver = sql_driver
        self._column_stats_cache = {}

    async def replace_parameters(self, query: str) -> str:
        """
        Описание метода replace_parameters:
        Заменяет параметризованные заполнители в запросе значениями на основе статистики столбцов.

        Аргументы:
            query (str): SQL запрос с параметрами ($1, $2 и т.д.)

        Возвращает:
            str: Модифицированный запрос с замененными параметрами

        Исключения:
            ValueError: Если произошла ошибка при замене параметров
        """
        try:
            modified_query: str = query
            # Поиск всех заполнителей
            param_matches = list(re.finditer(r"\$\d+", query))
            if not param_matches:
                logger.debug(f"Параметры не найдены в запросе: {query[:50]}...")
                return query

            # Обработка специальных случаев
            # 1. LIMIT
            limit_pattern = re.compile(r"limit\s+\$(\d+)", re.IGNORECASE)
            modified_query = limit_pattern.sub(r"limit 100", modified_query)

            # 2. Статические INTERVAL
            interval_pattern = re.compile(r"interval\s+'(\d+)\s+([a-z]+)'", re.IGNORECASE)
            modified_query = interval_pattern.sub(lambda m: f"interval '2 {m.group(2)}'", modified_query)

            # 3. Параметризованные INTERVAL
            param_interval_pattern = re.compile(r"interval\s+\$(\d+)", re.IGNORECASE)
            modified_query = param_interval_pattern.sub("interval '2 days'", modified_query)

            # 4. OFFSET
            offset_pattern = re.compile(r"offset\s+\$(\d+)", re.IGNORECASE)
            modified_query = offset_pattern.sub(r"offset 0", modified_query)

            # Повторный поиск оставшихся параметров
            param_matches = list(re.finditer(r"\$\d+", modified_query))
            if not param_matches:
                return modified_query

            # Обработка BETWEEN
            between_pattern = re.compile(r"(\w+(?:\.\w+)?)\s+between\s+\$(\d+)\s+and\s+\$(\d+)", re.IGNORECASE)
            for match in between_pattern.finditer(query):
                column_ref, param1, param2 = match.groups()
                table_name: Optional[str] = None
                col_name: str = column_ref
                if "." in column_ref:
                    parts = column_ref.split(".")
                    alias, col_name = parts
                    table_columns = self.extract_columns(query)
                    for tbl, _cols in table_columns.items():
                        if any(alias == a for a in self._get_table_aliases(query, tbl)):
                            table_name = tbl
                            break
                else:
                    table_columns = self.extract_columns(query)
                    for tbl, cols in table_columns.items():
                        if col_name in cols:
                            table_name = tbl
                            break

                lower_bound: Union[int, float] = 10
                upper_bound: Union[int, float] = 100
                if table_name and col_name:
                    stats = await self._get_column_statistics(table_name, col_name)
                    if stats:
                        lower_bound = self._get_bound_values(stats, is_lower=True)
                        upper_bound = self._get_bound_values(stats, is_lower=False)

                param1_pattern = r"\$" + param1
                param2_pattern = r"\$" + param2
                modified_query = re.sub(param1_pattern, str(lower_bound), modified_query, count=1)
                modified_query = re.sub(param2_pattern, str(upper_bound), modified_query, count=1)

            # Повторный поиск оставшихся параметров
            param_matches = list(re.finditer(r"\$\d+", modified_query))
            if not param_matches:
                return modified_query

            table_columns = self.extract_columns(query)
            if not table_columns:
                return self._replace_parameters_generic(modified_query)

            # Обработка оставшихся параметров
            for match in reversed(param_matches):
                param_position: int = match.start()
                clause_start: int = max(
                    modified_query.rfind(" where ", 0, param_position),
                    modified_query.rfind(" and ", 0, param_position),
                    modified_query.rfind(" or ", 0, param_position),
                    modified_query.rfind(",", 0, param_position),
                    modified_query.rfind("(", 0, param_position),
                    -1,
                )
                if clause_start == -1:
                    clause_start = max(0, param_position - 100)

                preceding_text: str = modified_query[clause_start : param_position + 2]
                column_info = self._identify_parameter_column(preceding_text, table_columns)
                if column_info:
                    table_name, column_name = column_info
                    stats = await self._get_column_statistics(table_name, column_name)
                    replacement: str = self._get_replacement_value(stats, preceding_text) if stats else self._get_generic_replacement(preceding_text)
                else:
                    replacement = self._get_generic_replacement(preceding_text)

                modified_query = modified_query[: match.start()] + replacement + modified_query[match.end() :]

            return modified_query
        except Exception as e:
            logger.error(f"Ошибка при замене параметров: {e}", exc_info=True)
            raise ValueError("Ошибка при замене параметров") from e

    def _get_bound_values(self, stats: Dict[str, Any], is_lower: bool = True) -> Any:
        """
        Описание метода _get_bound_values:
        Возвращает подходящие граничные значения для диапазонных запросов на основе статистики столбца.

        Аргументы:
            stats (Dict[str, Any]): Статистика столбца из pg_stats
            is_lower (bool): True для нижней границы, False для верхней

        Возвращает:
            Any: Граничное значение
        """
        data_type: str = stats.get("data_type", "").lower()
        common_vals = stats.get("common_vals")
        common_freqs = stats.get("common_freqs")

        # Проверка наиболее частых значений
        if common_vals and common_freqs and len(common_vals) == len(common_freqs):
            common_vals_list: List[Any] = list(common_vals)
            common_freqs_list: List[float] = list(common_freqs)
            max_freq_idx: int = common_freqs_list.index(max(common_freqs_list))
            most_common: Any = common_vals_list[max_freq_idx]

            try:
                if isinstance(most_common, float):
                    adjustment = abs(most_common) * 0.05 if most_common != 0 else 1
                    return most_common - adjustment if is_lower else most_common + adjustment
                if isinstance(most_common, int):
                    adjustment = abs(most_common) * 0.05 if most_common != 0 else 1
                    return int(most_common - adjustment) if is_lower else int(most_common + adjustment)
                elif isinstance(most_common, str) and most_common.isdigit():
                    num_val = float(most_common)
                    adjustment = abs(num_val) * 0.05 if num_val != 0 else 1
                    return str(int(num_val - adjustment)) if is_lower else str(int(num_val + adjustment))
                else:
                    return most_common
            except (TypeError, ValueError):
                logger.warning(f"Ошибка адаптации наиболее частого значения: {most_common}")
                return most_common

        # Проверка гистограммы
        histogram_bounds = stats.get("histogram_bounds")
        if histogram_bounds and len(histogram_bounds) >= 3:
            median_idx: int = len(histogram_bounds) // 2
            idx_offset: int = max(1, len(histogram_bounds) // 10)
            bound_idx: int = max(0, median_idx - idx_offset) if is_lower else min(len(histogram_bounds) - 1, median_idx + idx_offset)
            return histogram_bounds[bound_idx]

        # Использование наиболее частого значения
        most_common = stats.get("most_common_vals", [None])[0] if stats.get("most_common_vals") else None
        if most_common is not None:
            return most_common

        # Значения по умолчанию
        if "int" in data_type or data_type in ["smallint", "integer", "bigint"]:
            return 10 if is_lower else 20
        elif data_type in ["numeric", "decimal", "real", "double precision", "float"]:
            return 10.0 if is_lower else 20.0
        elif "date" in data_type or "time" in data_type:
            return "'2023-01-01'" if is_lower else "'2023-01-31'"
        elif data_type == "boolean":
            return "true"
        else:
            return "'m'" if is_lower else "'n'"

    def _get_table_aliases(self, query: str, table_name: str) -> List[str]:
        """
        Описание метода _get_table_aliases:
        Извлекает псевдонимы таблицы из запроса.

        Аргументы:
            query (str): SQL запрос
            table_name (str): Имя таблицы

        Возвращает:
            List[str]: Список псевдонимов, включая имя таблицы
        """
        try:
            parsed = parse_sql(query)
            if not parsed:
                return [table_name]

            stmt = parsed[0].stmt
            alias_visitor = TableAliasVisitor()
            alias_visitor(stmt)

            aliases: List[str] = [table_name]
            for alias, table in alias_visitor.aliases.items():
                if table.lower() == table_name.lower():
                    aliases.append(alias)

            return aliases
        except Exception as e:
            logger.error(f"Ошибка при извлечении псевдонимов таблицы: {e}", exc_info=True)
            return [table_name]

    def _identify_parameter_column(self, context: str, table_columns: Dict[str, Set[str]]) -> Optional[Tuple[str, str]]:
        """
        Описание метода _identify_parameter_column:
        Определяет, к какому столбцу относится параметр на основе контекста.

        Аргументы:
            context (str): Контекст запроса
            table_columns (Dict[str, Set[str]]): Столбцы, сгруппированные по таблицам

        Возвращает:
            Optional[Tuple[str, str]]: Кортеж (таблица, столбец) или None
        """
        for table, columns in table_columns.items():
            for column in columns:
                patterns = [
                    rf"{column}\s*=\s*\$\d+",
                    rf"{column}\s+in\s+\([^)]*\$\d+[^)]*\)",
                    rf"{column}\s+like\s+\$\d+",
                    rf"{column}\s*>\s*\$\d+",
                    rf"{column}\s*<\s*\$\d+",
                    rf"{column}\s*>=\s*\$\d+",
                    rf"{column}\s*<=\s*\$\d+",
                    rf"{column}\s+between\s+\$\d+\s+and\s+\$\d+",
                ]
                for pattern in patterns:
                    if re.search(pattern, context, re.IGNORECASE):
                        return (table, column)
        return None

    async def _get_column_statistics(self, table_name: str, column_name: str) -> Optional[Dict[str, Any]]:
        """
        Описание метода _get_column_statistics:
        Получает статистику столбца из pg_stats.

        Аргументы:
            table_name (str): Имя таблицы
            column_name (str): Имя столбца

        Возвращает:
            Optional[Dict[str, Any]]: Статистика столбца или None
        """
        cache_key: str = f"{table_name}.{column_name}"
        if cache_key in self._column_stats_cache:
            return self._column_stats_cache[cache_key]

        try:
            query: str = """
            SELECT
                data_type,
                most_common_vals as common_vals,
                most_common_freqs as common_freqs,
                histogram_bounds,
                null_frac,
                n_distinct,
                correlation
            FROM pg_stats
            JOIN information_schema.columns
                ON pg_stats.tablename = information_schema.table_name
                AND pg_stats.attname = information_schema.column_name
            WHERE pg_stats.tablename = {}
            AND pg_stats.attname = {}
            """
            result = await SafeSqlDriver.execute_param_query(self.sql_driver, query, [table_name, column_name])
            if not result or not result[0]:
                self._column_stats_cache[cache_key] = None
                return None

            stats: Dict[str, Any] = dict(result[0].cells)
            for key in ["common_vals", "common_freqs", "histogram_bounds"]:
                if key in stats and stats[key] is not None:
                    if isinstance(stats[key], str):
                        array_str = stats[key].strip("{}")
                        if array_str:
                            stats[key] = [self._parse_pg_array_value(val) for val in array_str.split(",")]
                        else:
                            stats[key] = []

            self._column_stats_cache[cache_key] = stats
            return stats
        except Exception as e:
            logger.warning(f"Ошибка при получении статистики для {table_name}.{column_name}: {e}")
            self._column_stats_cache[cache_key] = None
            return None

    def _parse_sql_array_value(self, value: str) -> Any:
        """
        Описание метода _parse_sql_array_value:
        Парсит значение из массива PostgreSQL.

        Аргументы:
            value (str): Строковое представление значения

        Возвращает:
            Any: Распарсенное значение
        """
        value = value.strip()
        if value == "null":
            return None
        elif value.startswith('"') and value.endswith('"'):
            return value[1:-1]
        try:
            if "." in value:
                return float(value)
            else:
                return int(value)
        except ValueError:
            return value

    def _get_replacement_value(self, stats: Dict[str, Any], context: str) -> str:
        """
        Описание метода _get_replacement_value:
        Генерирует заменяемое значение на основе статистики столбца и контекста.

        Аргументы:
            stats (Dict[str, Any]): Статистика столбца
            context (str): Контекст запроса

        Возвращает:
            str: Значение для замены
        """
        data_type: str = stats.get("data_type", "").lower()
        common_vals = stats.get("common_vals")
        histogram_bounds = stats.get("histogram_bounds")

        is_equality: bool = "=" in context and "!=" not in context and "<>" not in context
        is_range: bool = any(op in context for op in [">", "<", ">=", "<=", "between"])
        is_like: bool = "like" in context

        # Строки
        if "char" in data_type or data_type == "text":
            if is_like:
                return "'%test%'"
            elif common_vals and is_equality:
                sample = common_vals[0]
                return f"'{sample}'"
            elif common_vals:
                sample = common_vals[0]
                return f"'{sample}'"
            else:
                return "'sample_value'"

        # Числа
        elif "int" in data_type or data_type in ["numeric", "decimal", "real", "double precision"]:
            if histogram_bounds and is_range:
                bounds = histogram_bounds
                if isinstance(bounds, list) and len(bounds) > 1:
                    middle_idx: int = len(bounds) // 2
                    value = bounds[middle_idx]
                    return str(value)
            elif common_vals and is_equality:
                return str(common_vals[0])
            elif histogram_bounds:
                bounds = histogram_bounds
                if isinstance(bounds, list) and len(bounds) > 0:
                    return str(bounds[0])
            return "41" if "int" in data_type else "41.5"

        # Даты/время
        elif "date" in data_type or "time" in data_type:
            return "'2023-01-15'" if is_range else "'2023-01-01'"

        # Булевы значения
        elif data_type == "boolean":
            return "true"

        # По умолчанию
        return "'sample_value'"

    def _get_generic_replacement(self, context: str) -> str:
        """
        Описание метода _get_generic_replacement:
        Возвращает общее заменяемое значение, когда тип столбца неизвестен.

        Аргументы:
            context (str): Контекст запроса

        Возвращает:
            str: Значение для замены
        """
        context = context.lower()
        if any(date_word in context.split() for date_word in ["date", "timestamp", "time"]):
            return "'2023-01-01'"
        if any(word in context for word in ["id", "key", "code", "num"]):
            return "43"
        if "like" in context:
            return "'%sample%'"
        if any(word in context for word in ["amount", "price", "cost", "fee"]):
            return "99.99"
        if any(op in context for op in ["=", ">", "<", ">=", "<="]):
            return "44"
        return "'sample_value'"

    def _replace_parameters_generic(self, query: str) -> str:
        """
        Описание метода _replace_parameters_generic:
        Выполняет общую замену параметров, если доступ к каталогу невозможен.

        Аргументы:
            query (str): SQL запрос

        Возвращает:
            str: Модифицированный запрос
        """
        try:
            modified_query: str = query
            modified_query = re.sub(r"like\s+\$\d+", "like '%'", modified_query)
            modified_query = re.sub(
                r"(\w+)\s*=\s*\$\d+",
                lambda m: self._context_replace(m, "="),
                modified_query,
            )
            modified_query = re.sub(
                r"(\w+)\s*<\s*\$\d+",
                lambda m: self._context_replace(m, "<"),
                modified_query,
            )
            modified_query = re.sub(
                r"(\w+)\s*>\s*\$\d+",
                lambda m: self._context_replace(m, ">"),
                modified_query,
            )
            modified_query = re.sub(r"(\d+) and \$\d+", r"\1 and 100", modified_query)
            modified_query = re.sub(r"\$\d+ and (\d+)", r"1 and \1", modified_query)
            modified_query = re.sub(r">\s*\$\d+", "> 1", modified_query)
            modified_query = re.sub(r"<\s*\$\d+", "< 100", modified_query)
            modified_query = re.sub(r"=\s*\$\d+\b", "= 45", modified_query)
            modified_query = re.sub(r"\$\d+", "'65535'", modified_query)
            return modified_query
        except Exception as e:
            logger.error(f"Ошибка при общей замене параметров: {e}", exc_info=True)
            return query

    def _context_replace(self, match: re.Match, op: str) -> str:
        """
        Описание метода _context_replace:
        Заменяет параметры на основе контекста имени столбца.

        Аргументы:
            match (re.Match): Совпадение регулярного выражения
            op (str): Оператор сравнения

        Возвращает:
            str: Замененная строка
        """
        col_name: str = match.group(1).lower()
        if col_name.endswith("id") or col_name == "id":
            return f"{col_name} {op} 46"
        if any(word in col_name for word in ["date", "time", "created", "updated"]):
            return f"{col_name} {op} '2023-01-01'"
        if any(word in col_name for word in ["amount", "price", "cost", "count", "num", "qty"]):
            return f"{col_name} {op} 46.5"
        if "status" in col_name or "type" in col_name or "state" in col_name:
            return f"{col_name} {op} 'active'"
        return f"{col_name} {op} 'sample_value'"

    def extract_columns(self, query: str) -> Dict[str, Set[str]]:
        """
        Описание метода extract_columns:
        Извлекает столбцы из запроса с использованием улучшенных visitor'ов.

        Аргументы:
            query (str): SQL запрос

        Возвращает:
            Dict[str, Set[str]]: Словарь столбцов, сгруппированных по таблицам
        """
        try:
            parsed = parse_sql(query)
            if not parsed:
                return {}
            stmt = parsed[0].stmt
            if not isinstance(stmt, SelectStmt):
                return {}
            return self.extract_stmt_columns(stmt)
        except Exception:
            logger.warning(f"Ошибка при извлечении столбцов из запроса: {query}")
            return {}

    def extract_stmt_columns(self, stmt: SelectStmt) -> Dict[str, Set[str]]:
        """
        Описание метода extract_stmt_columns:
        Извлекает столбцы из SELECT запроса.

        Аргументы:
            stmt (SelectStmt): Узел SELECT запроса

        Возвращает:
            Dict[str, Set[str]]: Словарь столбцов, сгруппированных по таблицам
        """
        try:
            collector = ColumnCollector()
            collector(stmt)
            return collector.columns
        except Exception:
            logger.error(f"Ошибка при извлечении столбцов из SELECT: {stmt}")
            return {}
