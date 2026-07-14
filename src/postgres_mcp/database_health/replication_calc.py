# Анализ файла replication_calc.py
#
# Описание файла:
# Файл replication_calc.py содержит класс ReplicationCalc, который предоставляет функциональность
# для проверки состояния репликации в базе данных PostgreSQL. Класс анализирует, является ли база данных
# репликой, проверяет задержку репликации, активность репликации и состояние слотов репликации.
# Также включает классы данных ReplicationSlot и ReplicationMetrics для хранения метрик.
#
# Используемые модули:
# - dataclasses: для создания классов данных
# - typing: для аннотаций типов
#
# Импорты из пакета:
# - SqlDriver: для взаимодействия с базой данных PostgreSQL
#
# Основные компоненты:
# - Класс ReplicationSlot: класс данных для хранения информации о слотах репликации
# - Класс ReplicationMetrics: класс данных для хранения метрик репликации
# - Класс ReplicationCalc: основной класс для проверки состояния репликации
#
# Зависимости:
# Файл зависит от модуля sql (sql_driver.py), который предоставляет интерфейс SqlDriver.
# Требуется доступ к системным представлениям PostgreSQL (pg_stat_replication, pg_replication_slots и др.).
#
# Примечания:
# - Класс использует асинхронные методы для выполнения запросов к базе данных.
# - Поддержка функций проверяется с учетом версии PostgreSQL.
# - Результаты возвращаются в виде строк для удобства представления.

from dataclasses import dataclass
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ..sql import SqlDriver


# Описание класса ReplicationSlot
#
# Класс ReplicationSlot — это класс данных, который хранит информацию о слоте репликации,
# включая его имя, базу данных и статус активности.
@dataclass
class ReplicationSlot:
    """Информация о слоте репликации."""

    slot_name: str
    database: str
    active: bool


# Описание класса ReplicationMetrics
#
# Класс ReplicationMetrics — это класс данных, который хранит метрики репликации,
# включая статус реплики, задержку репликации, активность репликации и список слотов.
@dataclass
class ReplicationMetrics:
    """Метрики состояния репликации базы данных."""

    is_replica: bool
    replication_lag_seconds: Optional[float]
    is_replicating: bool
    replication_slots: List[ReplicationSlot]


# Описание класса ReplicationCalc
#
# Класс ReplicationCalc предоставляет методы для проверки состояния репликации базы данных,
# включая проверку статуса реплики, задержки репликации и слотов репликации.
class ReplicationCalc:
    """Калькулятор состояния репликации базы данных PostgreSQL."""

    def __init__(self, sql_driver: SqlDriver) -> None:
        """
        Описание метода __init__:
        Инициализирует объект ReplicationCalc.

        Аргументы:
            sql_driver (SqlDriver): Драйвер для взаимодействия с базой данных

        Возвращает:
            None
        """
        self.sql_driver: SqlDriver = sql_driver
        self._server_version: Optional[int] = None
        self._feature_support: Dict[str, bool] = {}

    async def replication_health_check(self) -> str:
        """
        Описание метода replication_health_check:
        Проверяет состояние репликации, включая задержку и слоты.

        Возвращает:
            str: Текстовое описание состояния репликации
        """
        metrics: ReplicationMetrics = await self._get_replication_metrics()
        result: List[str] = []

        if metrics.is_replica:
            result.append("Это база данных-реплика.")
            if not metrics.is_replicating:
                result.append("ВНИМАНИЕ: Реплика не активно реплицируется с первичной базы!")
            else:
                result.append("Реплика активно реплицируется с первичной базы.")

            if metrics.replication_lag_seconds is not None:
                if metrics.replication_lag_seconds == 0:
                    result.append("Задержка репликации отсутствует.")
                else:
                    result.append(f"Задержка репликации: {metrics.replication_lag_seconds:.1f} секунд")
        else:
            result.append("Это первичная база данных.")
            if metrics.is_replicating:
                result.append("Имеются активные подключенные реплики.")
            else:
                result.append("Активные реплики не подключены.")

        if metrics.replication_slots:
            active_slots = [s for s in metrics.replication_slots if s.active]
            inactive_slots = [s for s in metrics.replication_slots if not s.active]

            if active_slots:
                result.append("\nАктивные слоты репликации:")
                for slot in active_slots:
                    result.append(f"- {slot.slot_name} (база данных: {slot.database})")

            if inactive_slots:
                result.append("\nНеактивные слоты репликации:")
                for slot in inactive_slots:
                    result.append(f"- {slot.slot_name} (база данных: {slot.database})")
        else:
            result.append("\nСлоты репликации не найдены.")

        return "\n".join(result)

    async def _get_replication_metrics(self) -> ReplicationMetrics:
        """
        Описание метода _get_replication_metrics:
        Получает полные метрики репликации.

        Возвращает:
            ReplicationMetrics: Объект с метриками репликации
        """
        return ReplicationMetrics(
            is_replica=await self._is_replica(),
            replication_lag_seconds=await self._get_replication_lag(),
            is_replicating=await self._is_replicating(),
            replication_slots=await self._get_replication_slots(),
        )

    async def _is_replica(self) -> bool:
        """
        Описание метода _is_replica:
        Проверяет, является ли база данных репликой.

        Возвращает:
            bool: True, если база данных является репликой
        """
        result = await self.sql_driver.execute_query("SELECT pg_is_in_recovery()")
        result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result] if result is not None else []
        return bool(result_list[0]["pg_is_in_recovery"]) if result_list else False

    async def _get_replication_lag(self) -> Optional[float]:
        """
        Описание метода _get_replication_lag:
        Получает задержку репликации в секундах.

        Возвращает:
            Optional[float]: Задержка репликации или None, если функция недоступна
        """
        if not self._feature_supported("replication_lag"):
            return None

        lag_condition: str = (
            "pg_last_wal_receive_lsn() = pg_last_wal_replay_lsn()"
            if await self._get_server_version() >= 100000
            else "pg_last_xlog_receive_location() = pg_last_xlog_replay_location()"
        )

        try:
            result = await self.sql_driver.execute_query(f"""
                SELECT
                    CASE
                        WHEN NOT pg_is_in_recovery() OR {lag_condition} THEN 0
                        ELSE EXTRACT (EPOCH FROM NOW() - pg_last_xact_replay_timestamp())
                    END
                AS replication_lag
            """)
            result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result] if result is not None else []
            return float(result_list[0]["replication_lag"]) if result_list else None
        except Exception:
            self._feature_support["replication_lag"] = False
            return None

    async def _get_replication_slots(self) -> List[ReplicationSlot]:
        """
        Описание метода _get_replication_slots:
        Получает информацию о слотах репликации.

        Возвращает:
            List[ReplicationSlot]: Список объектов ReplicationSlot
        """
        if await self._get_server_version() < 90400 or not self._feature_supported("replication_slots"):
            return []

        try:
            result = await self.sql_driver.execute_query("""
                SELECT
                    slot_name,
                    database,
                    active
                FROM pg_replication_slots
            """)
            if result is None:
                return []
            result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result]
            return [
                ReplicationSlot(
                    slot_name=row["slot_name"],
                    database=row["database"],
                    active=row["active"],
                )
                for row in result_list
            ]
        except Exception:
            self._feature_support["replication_slots"] = False
            return []

    async def _is_replicating(self) -> bool:
        """
        Описание метода _is_replicating:
        Проверяет, активно ли выполняется репликация.

        Возвращает:
            bool: True, если репликация активна
        """
        if not self._feature_supported("replicating"):
            return False

        try:
            result = await self.sql_driver.execute_query("SELECT state FROM pg_stat_replication")
            result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result] if result is not None else []
            return bool(result_list and len(result_list) > 0)
        except Exception:
            self._feature_support["replicating"] = False
            return False

    async def _get_server_version(self) -> int:
        """
        Описание метода _get_server_version:
        Получает версию сервера PostgreSQL в числовом формате (например, 100000 для версии 10.0).

        Возвращает:
            int: Числовая версия сервера
        """
        if self._server_version is None:
            result = await self.sql_driver.execute_query("SHOW server_version_num")
            result_list: List[Dict[str, Any]] = [dict(x.cells) for x in result] if result is not None else []
            self._server_version = int(result_list[0]["server_version_num"]) if result_list else 0
        return self._server_version

    def _feature_supported(self, feature: str) -> bool:
        """
        Описание метода _feature_supported:
        Проверяет, поддерживается ли указанная функция, и кэширует результат.

        Аргументы:
            feature (str): Название функции для проверки

        Возвращает:
            bool: True, если функция поддерживается
        """
        return self._feature_support.get(feature, True)
