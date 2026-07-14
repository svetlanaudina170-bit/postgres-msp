# Анализ файла __init__.py
#
# Описание файла:
# Файл __init__.py определяет содержимое модуля top_queries, экспортируя константу PG_STAT_STATEMENTS и класс TopQueriesCalc
# из подмодуля top_queries_calc. Он используется для упрощения импорта этих элементов на уровне пакета top_queries.
#
# Используемые модули:
# - Отсутствуют (только относительный импорт из подмодуля top_queries_calc)
#
# Импорты:
# - PG_STAT_STATEMENTS: константа, представляющая имя расширения PostgreSQL для статистики запросов
# - TopQueriesCalc: класс для анализа наиболее ресурсоемких или медленных запросов
#
# Основные компоненты:
# - Переменная __all__: список экспортируемых элементов модуля
#
# Зависимости:
# Файл связан с top_queries_calc.py, откуда импортируются PG_STAT_STATEMENTS и TopQueriesCalc.
# Используется в контексте пакета, взаимодействующего с PostgreSQL, в частности с расширением pg_stat_statements.

from .top_queries_calc import PG_STAT_STATEMENTS  # Импорт константы PG_STAT_STATEMENTS
from .top_queries_calc import TopQueriesCalc  # Импорт класса TopQueriesCalc

# Определение экспортируемых элементов модуля
__all__: list[str] = [
    "PG_STAT_STATEMENTS",  # Константа для имени расширения PostgreSQL
    "TopQueriesCalc",  # Класс для анализа запросов
]
