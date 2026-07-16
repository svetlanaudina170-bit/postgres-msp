# =========================================================================
# VERSION: 1.0.0
# Path: src/postgres_mcp/sql_editor/history.py
# История SQL-запросов (JSON-файл)
# =========================================================================

import json
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_HISTORY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "sql_history.json",
)

_HISTORY_MAX = 200


class QueryHistory:
    """Управление историей SQL-запросов.

    Хранит: SQL-текст, тип оператора, длительность, кол-во строк, ошибку, метку времени.
    """

    def __init__(self, path: str = _DEFAULT_HISTORY_PATH):
        self.path = path
        self._entries: list[dict] = []
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self._entries = data[-_HISTORY_MAX:]
        except Exception as e:
            logger.warning(f"Failed to load query history: {e}")
            self._entries = []

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._entries[-_HISTORY_MAX:], f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save query history: {e}")

    def add(
        self,
        sql: str,
        stmt_type: str = "SELECT",
        duration_ms: float = 0,
        row_count: int = 0,
        error: Optional[str] = None,
    ):
        entry = {
            "sql": sql.strip(),
            "type": stmt_type,
            "duration_ms": round(duration_ms, 1),
            "row_count": row_count,
            "error": error,
            "timestamp": datetime.now().isoformat(),
        }
        self._entries.append(entry)
        self._save()

    def get_recent(self, limit: int = 50) -> list[dict]:
        return list(reversed(self._entries[-limit:]))

    def get_by_type(self, stmt_type: str, limit: int = 20) -> list[dict]:
        filtered = [e for e in self._entries if e["type"] == stmt_type.upper()]
        return list(reversed(filtered[-limit:]))

    def get_favorites(self) -> list[dict]:
        return [e for e in reversed(self._entries) if e.get("favorite")]

    def toggle_favorite(self, index_from_end: int):
        entries = list(reversed(self._entries))
        if 0 <= index_from_end < len(entries):
            idx = len(self._entries) - 1 - index_from_end
            self._entries[idx]["favorite"] = not self._entries[idx].get("favorite", False)
            self._save()
            return self._entries[idx]["favorite"]
        return False

    def clear(self):
        self._entries = []
        self._save()

    def count(self) -> int:
        return len(self._entries)


# Глобальный singleton для использования в UI
_history_instance: Optional[QueryHistory] = None


def get_history() -> QueryHistory:
    global _history_instance
    if _history_instance is None:
        _history_instance = QueryHistory()
    return _history_instance
