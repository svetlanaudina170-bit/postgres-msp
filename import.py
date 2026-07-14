# =========================================================================
# VERSION: 1.1.0
# Path: import.py
# (утилита-сканер импортов проекта — standalone-скрипт, не часть
#  runtime-приложения postgres_mcp)
# Изменения:
#  - Исправлен project_path: обычная строка 'C:\Projects\...' содержала
#    невалидные escape-последовательности (\M, \p, \s не являются
#    распознаваемыми escape-кодами Python — интерпретатор молча
#    оставлял их как есть, но это было "случайно работает", а не
#    осознанно корректно, и могло дать SyntaxWarning на новых версиях
#    Python). Заменено на raw-строку r'...' — путь теперь читается
#    буквально, без риска, что случайная будущая правка добавит
#    настоящую escape-последовательность (например, \n) и молча
#    сломает путь.
# =========================================================================

import os
import re
from pathlib import Path


def extract_imports(project_path):
    # Множество для хранения уникальных имён пакетов/модулей
    unique_imports = set()

    # Регулярное выражение для поиска импортов
    # Соответствует 'import module' или 'from module import ...'
    import_pattern = re.compile(
        r"^\s*(?:import|from)\s+([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)",
        re.MULTILINE,
    )

    # Обход всех файлов в директории
    for root, _, files in os.walk(project_path):
        for file in files:
            if file.endswith(".py"):
                file_path = Path(root) / file
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        content = f.read()
                        # Поиск всех импортов в файле
                        matches = import_pattern.findall(content)
                        for match in matches:
                            # Разделяем по точкам и берём первый элемент (название пакета/модуля)
                            module_name = match.split(".")[0]
                            unique_imports.add(module_name)
                except Exception as e:
                    print(f"Ошибка при чтении файла {file_path}: {e}")

    # Возвращаем отсортированный список уникальных импортов
    return sorted(unique_imports)


# Пример использования
project_path = r"C:\Projects\MCP\postgres-mcp\postgres-mcp\src\postgres_mcp"  # Укажите путь к корневой папке проекта
imports = extract_imports(project_path)
print("Уникальные импорты:")
for imp in imports:
    print(imp)
