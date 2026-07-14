# Анализ файла index.py
#
# Описание файла:
# Файл index.py содержит класс IndexDefinition, который представляет неизменяемую конфигурацию индекса для PostgreSQL.
# Класс используется для создания и управления определениями индексов, включая их таблицу, столбцы и метод индексирования.
# Он поддерживает генерацию SQL-запросов для создания индексов и формирование их имен в соответствии с заданной конвенцией.
#
# Используемые модули:
# - dataclasses: для создания класса IndexDefinition с минимальным boilerplate-кодом
# - typing: для аннотаций типов (Any, tuple)
#
# Импорты:
# - Отсутствуют (только стандартные библиотеки Python)
#
# Основные компоненты:
# - Класс IndexDefinition: неизменяемый (frozen) класс для конфигурации индекса
#
# Зависимости:
# Файл не имеет прямых зависимостей от других модулей пакета, но может использоваться в контексте
# модулей, взаимодействующих с PostgreSQL, таких как server.py или top_queries_calc.py,
# где требуется работа с индексами.

from dataclasses import dataclass
from typing import Any, Tuple, Dict

# Описание класса IndexDefinition
#
# Класс IndexDefinition представляет конфигурацию индекса в PostgreSQL.
# Он является неизменяемым (frozen=True) и хранит информацию о таблице, столбцах и методе индексирования.
# Предоставляет методы для генерации SQL-выражения и имени индекса.
@dataclass(frozen=True)
class IndexDefinition:
    """Неизменяемая конфигурация индекса для хэширования."""

    table: str  # Имя таблицы, для которой создается индекс
    columns: Tuple[str, ...]  # Кортеж столбцов, входящих в индекс
    using: str = "btree"  # Метод индексирования (по умолчанию btree)

    def to_dict(self) -> Dict[str, Any]:
        """
        Описание метода to_dict:
        Преобразует конфигурацию индекса в словарь.

        Возвращает:
            Dict[str, Any]: Словарь с полями table, columns, using и definition
        """
        return {
            "table": self.table,
            "columns": list(self.columns),
            "using": self.using,
            "definition": self.definition,
        }

    @property
    def definition(self) -> str:
        """
        Описание свойства definition:
        Генерирует SQL-выражение для создания индекса.

        Возвращает:
            str: SQL-выражение CREATE INDEX
        """
        return f"CREATE INDEX {self.name} ON {self.table} USING {self.using} ({', '.join(self.columns)})"

    @property
    def name(self) -> str:
        """
        Описание свойства name:
        Генерирует имя индекса на основе таблицы, столбцов и метода индексирования.
        Очищает имена столбцов от специальных символов для корректного формирования имени.

        Возвращает:
            str: Сгенерированное имя индекса
        """
        # Очистка имен столбцов для использования в имени индекса
        cleaned_columns: List[str] = []
        for col in self.columns:
            # Замена специальных символов на подчеркивания
            cleaned_col: str = col.replace("(", "_").replace(")", "_").replace(" ", "_").replace(",", "_")
            # Удаление последовательных подчеркиваний
            while "__" in cleaned_col:
                cleaned_col = cleaned_col.replace("__", "_")
            # Удаление конечных подчеркиваний
            cleaned_col = cleaned_col.rstrip("_")
            cleaned_columns.append(cleaned_col)

        # Формирование части имени из столбцов
        column_part: str = "_".join(cleaned_columns)
        # Добавление суффикса для метода индексирования, если не btree
        suffix: str = "" if self.using == "btree" else f"_{self.using}"
        # Формирование базового имени индекса
        base: str = f"crystaldba_idx_{self.table}_{column_part}_{len(self.columns)}"
        return f"{base}{suffix}"

    def __str__(self) -> str:
        """
        Описание метода __str__:
        Возвращает строковое представление индекса в виде SQL-выражения.

        Возвращает:
            str: SQL-выражение CREATE INDEX
        """
        return self.definition

    def __repr__(self) -> str:
        """
        Описание метода __repr__:
        Возвращает формальное строковое представление объекта IndexDefinition.

        Возвращает:
            str: Строковое представление объекта
        """
        return f"IndexDefinition(table='{self.table}', columns={self.columns}, using='{self.using}')"