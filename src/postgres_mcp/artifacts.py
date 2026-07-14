# Анализ файла artifacts.py
#
# Описание файла:
# Файл artifacts.py содержит классы и функции для работы с артефактами, связанными с анализом планов выполнения SQL запросов в PostgreSQL,
# в частности для Database Tuning Advisor. Основная цель — обработка и представление данных плана выполнения (EXPLAIN),
# включая создание древовидной структуры плана, форматирование в текстовом виде и сравнение планов для выявления изменений.
#
# Используемые модули:
# - difflib: для генерации текстовых различий между планами
# - json: для сериализации данных плана
# - typing: для аннотаций типов
# - attrs: для декларативного определения классов с атрибутами
#
# Основные компоненты:
# - Константа INFINITE_IMPROVEMENT_MULTIPLIER: значение для бесконечного улучшения производительности
# - Класс ErrorResult: для представления ошибок
# - Функция calculate_improvement_multiple: для вычисления множителя улучшения производительности
# - Класс PlanNode: для представления узла плана выполнения
# - Класс ExplainPlanArtifact: для представления полного плана выполнения с методами форматирования и сравнения
#
# Зависимости:
# Файл используется в связке с server.py, где ExplainPlanArtifact и ErrorResult применяются для обработки результатов EXPLAIN запросов.

"""Артефакты для Database Tuning Advisor."""

import difflib
import json
from typing import Any
from typing import List
from typing import Optional

from attrs import define
from attrs import field

# Константа для обозначения бесконечного улучшения производительности
# Если стоимость рекомендации равна 0.0, возвращается это значение
INFINITE_IMPROVEMENT_MULTIPLIER: float = 1000000.0


# Описание класса ErrorResult
#
# Класс ErrorResult представляет простую ошибку с текстовым сообщением.
# Используется для возврата ошибок при обработке планов выполнения.
class ErrorResult:
    """Простой класс для представления результата ошибки."""

    value: str  # Текстовое сообщение об ошибке

    def __init__(self, message: str) -> None:
        """
        Описание метода __init__:
        Инициализирует объект ошибки с заданным сообщением.

        Аргументы:
            message (str): Сообщение об ошибке

        Возвращает:
            None
        """
        # Сохранение сообщения об ошибке
        self.value = message

    def to_text(self) -> str:
        """
        Описание метода to_text:
        Возвращает текстовое представление ошибки.

        Возвращает:
            str: Сообщение об ошибке
        """
        # Возврат сохраненного сообщения
        return self.value


def calculate_improvement_multiple(base_cost: float, rec_cost: float) -> float:
    """
    Описание функции calculate_improvement_multiple:
    Вычисляет множитель улучшения производительности на основе базовой и рекомендованной стоимости выполнения.

    Аргументы:
        base_cost (float): Базовая стоимость выполнения запроса
        rec_cost (float): Рекомендованная стоимость выполнения запроса

    Возвращает:
        float: Множитель улучшения (base_cost / rec_cost или специальные значения)
    """
    # Проверка базовой стоимости
    if base_cost <= 0.0:
        # Если базовая стоимость <= 0, возвращаем 1.0, так как деление невозможно
        return 1.0
    # Проверка рекомендованной стоимости
    if rec_cost <= 0.0:
        # Если рекомендованная стоимость <= 0, возвращаем INFINITE_IMPROVEMENT_MULTIPLIER
        return INFINITE_IMPROVEMENT_MULTIPLIER
    # Вычисление множителя улучшения
    return base_cost / rec_cost


# Описание класса PlanNode
#
# Класс PlanNode представляет узел в дереве плана выполнения SQL запроса.
# Содержит информацию о типе операции, стоимости, строках, а также опциональные метрики из ANALYZE и буферов.
@define
class PlanNode:
    """Узел плана выполнения SQL запроса."""

    node_type: str  # Тип узла (например, Seq Scan, Index Scan)
    total_cost: float  # Общая стоимость выполнения узла
    startup_cost: float  # Стоимость запуска узла
    plan_rows: int  # Ожидаемое количество строк
    plan_width: int  # Средняя ширина строки в байтах

    # Метрики из ANALYZE (опциональные)
    actual_total_time: Optional[float] = field(default=None)  # Фактическое общее время выполнения
    actual_startup_time: Optional[float] = field(default=None)  # Фактическое время запуска
    actual_rows: Optional[int] = field(default=None)  # Фактическое количество строк
    actual_loops: Optional[int] = field(default=None)  # Количество циклов выполнения

    # Информация о буферах (опциональная)
    shared_hit_blocks: Optional[int] = field(default=None)  # Количество блоков, попавших в кэш
    shared_read_blocks: Optional[int] = field(default=None)  # Количество прочитанных блоков
    shared_written_blocks: Optional[int] = field(default=None)  # Количество записанных блоков

    # Дополнительные поля
    relation_name: Optional[str] = field(default=None)  # Имя связанной таблицы
    filter: Optional[str] = field(default=None)  # Условие фильтрации
    children: List["PlanNode"] = field(factory=list)  # Список дочерних узлов

    @classmethod
    def from_json_data(cls, json_node: dict[str, Any]) -> "PlanNode":
        """
        Описание метода from_json_data:
        Создает объект PlanNode из JSON данных плана выполнения.

        Аргументы:
            json_node (dict[str, Any]): JSON данные узла плана

        Возвращает:
            PlanNode: Объект узла плана
        """
        # Извлечение основных полей
        node: PlanNode = cls(
            node_type=json_node["Node Type"],
            total_cost=json_node["Total Cost"],
            startup_cost=json_node["Startup Cost"],
            plan_rows=json_node["Plan Rows"],
            plan_width=json_node["Plan Width"],
        )

        # Извлечение опциональных полей ANALYZE
        if "Actual Total Time" in json_node:
            node.actual_total_time = json_node["Actual Total Time"]
            node.actual_startup_time = json_node["Actual Startup Time"]
            node.actual_rows = json_node["Actual Rows"]
            node.actual_loops = json_node["Actual Loops"]

        # Извлечение опциональных полей буферов
        if "Shared Hit Blocks" in json_node:
            node.shared_hit_blocks = json_node["Shared Hit Blocks"]
            node.shared_read_blocks = json_node["Shared Read Blocks"]
            node.shared_written_blocks = json_node["Shared Written Blocks"]

        # Извлечение общих опциональных полей
        if "Relation Name" in json_node:
            node.relation_name = json_node["Relation Name"]
        if "Filter" in json_node:
            node.filter = json_node["Filter"]

        # Рекурсивная обработка дочерних узлов
        if "Plans" in json_node:
            node.children = [cls.from_json_data(child) for child in json_node["Plans"]]

        # Возврат созданного узла
        return node


# Описание класса ExplainPlanArtifact
#
# Класс ExplainPlanArtifact представляет полный план выполнения SQL запроса.
# Содержит текстовое представление, дерево плана и опциональные временные метрики.
@define
class ExplainPlanArtifact:
    """Артефакт плана выполнения SQL запроса."""

    value: str  # Текстовое представление плана в формате JSON
    plan_tree: PlanNode  # Дерево плана выполнения
    planning_time: Optional[float] = field(default=None)  # Время планирования запроса
    execution_time: Optional[float] = field(default=None)  # Время выполнения запроса

    def __init__(
        self,
        value: str,
        plan_tree: PlanNode,
        planning_time: Optional[float] = None,
        execution_time: Optional[float] = None,
    ) -> None:
        """
        Описание метода __init__:
        Инициализирует объект плана выполнения с заданными параметрами.

        Аргументы:
            value (str): Текстовое представление плана
            plan_tree (PlanNode): Дерево плана выполнения
            planning_time (Optional[float]): Время планирования
            execution_time (Optional[float]): Время выполнения

        Возвращает:
            None
        """
        # Сохранение переданных значений
        self.value = value
        self.plan_tree = plan_tree
        self.planning_time = planning_time
        self.execution_time = execution_time

    def to_text(self) -> str:
        """
        Описание метода to_text:
        Преобразует план выполнения в текстовое представление.

        Возвращает:
            str: Текстовое представление плана с информацией о времени
        """
        # Инициализация списка строк результата
        result: List[str] = []

        # Добавление информации о времени планирования
        if self.planning_time is not None:
            result.append(f"Время планирования: {self.planning_time:.3f} мс")
        # Добавление информации о времени выполнения
        if self.execution_time is not None:
            result.append(f"Время выполнения: {self.execution_time:.3f} мс")

        # Добавление представления дерева плана
        result.append(self._format_plan_node(self.plan_tree))

        # Объединение строк с переносами
        return "\n".join(result)

    @staticmethod
    def _format_plan_node(node: PlanNode, level: int = 0) -> str:
        """
        Описание метода _format_plan_node:
        Рекурсивно форматирует узел плана и его дочерние узлы в текстовое представление.

        Аргументы:
            node (PlanNode): Узел плана для форматирования
            level (int): Уровень отступа

        Возвращает:
            str: Текстовое представление узла и его дочерних узлов
        """
        # Формирование отступа
        indent: str = "  " * level
        # Основная строка с типом узла и стоимостью
        output: str = f"{indent}→ {node.node_type} (Стоимость: {node.startup_cost:.2f}..{node.total_cost:.2f})"

        # Добавление имени таблицы
        if node.relation_name:
            output += f" на {node.relation_name}"

        # Добавление информации о строках
        output += f" [Строк: {node.plan_rows}]"

        # Добавление фактических метрик
        if node.actual_total_time is not None:
            output += f" [Фактически: {node.actual_startup_time:.2f}..{node.actual_total_time:.2f} мс, Строк: {node.actual_rows}, Циклов: {node.actual_loops}]"

        # Добавление фильтра
        if node.filter:
            filter_text: str = node.filter
            # Обрезка длинных фильтров для читаемости
            if len(filter_text) > 100:
                filter_text = filter_text[:97] + "..."
            output += f"\n{indent}  Фильтр: {filter_text}"

        # Добавление информации о буферах
        if node.shared_hit_blocks is not None:
            output += (
                f"\n{indent}  Буферы - попаданий: {node.shared_hit_blocks}, чтений: {node.shared_read_blocks}, записей: {node.shared_written_blocks}"
            )

        # Рекурсивное форматирование дочерних узлов
        if node.children:
            for child in node.children:
                output += "\n" + ExplainPlanArtifact._format_plan_node(child, level + 1)

        # Возврат отформатированной строки
        return output

    @classmethod
    def from_json_data(cls, plan_data: dict[str, Any]) -> "ExplainPlanArtifact":
        """
        Описание метода from_json_data:
        Создает объект ExplainPlanArtifact из JSON данных плана выполнения.

        Аргументы:
            plan_data (dict[str, Any]): JSON данные плана

        Возвращает:
            ExplainPlanArtifact: Объект плана выполнения

        Исключения:
            ValueError: Если отсутствует поле 'Plan'
        """
        # Проверка наличия поля Plan
        if "Plan" not in plan_data:
            raise ValueError("Отсутствует поле 'Plan' в данных плана выполнения")

        # Создание дерева плана
        plan_tree: PlanNode = PlanNode.from_json_data(plan_data["Plan"])

        # Извлечение опциональной информации о времени
        planning_time: Optional[float] = plan_data.get("Planning Time")
        execution_time: Optional[float] = plan_data.get("Execution Time")

        # Создание и возврат объекта
        return cls(
            value=json.dumps(plan_data, indent=2),
            plan_tree=plan_tree,
            planning_time=planning_time,
            execution_time=execution_time,
        )

    @staticmethod
    def format_plan_summary(plan_data: dict[str, Any]) -> str:
        """
        Описание метода format_plan_summary:
        Извлекает и форматирует ключевую информацию из необработанных данных плана.

        Аргументы:
            plan_data (dict[str, Any]): Необработанные данные плана

        Возвращает:
            str: Краткое текстовое представление плана
        """
        # Проверка наличия данных
        if not plan_data:
            return "Данные плана недоступны"

        try:
            # Создание узла плана из JSON данных
            if "Plan" in plan_data:
                plan_node: PlanNode = PlanNode.from_json_data(plan_data["Plan"])
                # Форматирование дерева плана
                plan_tree: str = ExplainPlanArtifact._format_plan_node(plan_node, 0)
                return f"{plan_tree}"
            else:
                return "Недопустимые данные плана (отсутствует поле Plan)"

        except Exception as e:
            # Возврат сообщения об ошибке
            return f"Ошибка при суммировании плана: {e}"

    @staticmethod
    def create_plan_diff(before_plan: dict[str, Any], after_plan: dict[str, Any]) -> str:
        """
        Описание метода create_plan_diff:
        Генерирует текстовое сравнение двух планов выполнения.

        Аргументы:
            before_plan (dict[str, Any]): План выполнения до изменений
            after_plan (dict[str, Any]): План выполнения после изменений

        Возвращает:
            str: Текстовое представление различий между планами
        """
        # Проверка наличия данных планов
        if not before_plan or not after_plan:
            return "Невозможно создать сравнение: отсутствуют данные плана"

        try:
            # Создание деревьев планов
            before_tree: Optional[PlanNode] = PlanNode.from_json_data(before_plan["Plan"]) if "Plan" in before_plan else None
            after_tree: Optional[PlanNode] = PlanNode.from_json_data(after_plan["Plan"]) if "Plan" in after_plan else None

            # Проверка корректности структуры планов
            if not before_tree or not after_tree:
                return "Невозможно создать сравнение: некорректная структура плана"

            # Форматирование планов в текст
            before_lines: List[str] = ExplainPlanArtifact._format_plan_node(before_tree).split("\n")
            after_lines: List[str] = ExplainPlanArtifact._format_plan_node(after_tree).split("\n")

            # Инициализация списка строк различий
            diff_lines: List[str] = []
            diff_lines.append("ИЗМЕНЕНИЯ ПЛАНА:")
            diff_lines.append("------------")

            # Извлечение информации о стоимости
            before_cost: float = before_tree.total_cost
            after_cost: float = after_tree.total_cost
            improvement: float = calculate_improvement_multiple(before_cost, after_cost)

            # Добавление информации об улучшении стоимости
            diff_lines.append(f"Стоимость: {before_cost:.2f} → {after_cost:.2f} ({improvement:.1f}x улучшение)")
            diff_lines.append("")

            # Добавление изменений операций
            diff_lines.append("Изменения операций:")

            # Вспомогательная функция для извлечения типов узлов
            def extract_node_types(node: PlanNode, level: int = 0, result: Optional[List[str]] = None) -> List[str]:
                if result is None:
                    result = []
                indent: str = "  " * level
                node_info: str = f"{indent}→ {node.node_type}"
                if node.relation_name:
                    node_info += f" на {node.relation_name}"
                result.append(node_info)
                for child in node.children:
                    extract_node_types(child, level + 1, result)
                return result

            # Извлечение структур планов
            before_structure: List[str] = extract_node_types(before_tree)
            after_structure: List[str] = extract_node_types(after_tree)

            # Генерация структурных различий
            structure_diff: List[str] = list(
                difflib.unified_diff(
                    before_structure,
                    after_structure,
                    n=1,  # Количество строк контекста
                    lineterm="",
                )
            )

            # Добавление структурных различий
            if structure_diff:
                diff_lines.extend(structure_diff)
            else:
                diff_lines.append("Структурные изменения не обнаружены")

            # Добавление основных изменений
            diff_lines.append("")
            diff_lines.append("Основные изменения:")

            # Проверка изменения корневой операции
            if before_tree.node_type != after_tree.node_type:
                diff_lines.append(f"- Корневая операция изменена: {before_tree.node_type} → {after_tree.node_type}")

            # Сравнение последовательных сканирований
            before_scans: List[str] = [line for line in before_lines if "Seq Scan" in line]
            after_scans: List[str] = [line for line in after_lines if "Seq Scan" in line]
            if len(before_scans) > len(after_scans):
                diff_lines.append(f"- {len(before_scans) - len(after_scans)} последовательных сканирований заменены более эффективными методами")

            # Сравнение индексных сканирований
            before_idx_scans: List[str] = [line for line in before_lines if "Index Scan" in line]
            after_idx_scans: List[str] = [line for line in after_lines if "Index Scan" in line]
            if len(after_idx_scans) > len(before_idx_scans):
                diff_lines.append(f"- {len(after_idx_scans) - len(before_idx_scans)} новых индексных сканирований использовано")

            # Объединение строк различий
            return "\n".join(diff_lines)

        except Exception as e:
            # Возврат сообщения об ошибке
            return f"Ошибка при создании сравнения планов: {e}"
