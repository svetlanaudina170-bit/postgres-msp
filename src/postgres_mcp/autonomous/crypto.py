# =========================================================================
# VERSION: 1.0.0
# Path: src/postgres_mcp/autonomous/crypto.py
# Утилиты обратимого шифрования секретов в JSON-хранилищах подключений
# (connections.json для БД, llm_connections.json для LLM).
#
# Используется симметричное шифрование Fernet (AES-128-CBC + HMAC) из
# пакета cryptography. Ключ задаётся опционально через переменную окружения
# CONNECTIONS_ENCRYPTION_KEY (один ключ для обоих хранилищ).
#
# Поведение:
#   - Ключ НЕ задан (пусто): encrypt_value/decrypt_value возвращают значение
#     как есть (открытый текст, прежнее поведение). При старте логируется
#     warning, что секреты хранятся в открытом виде.
#   - Ключ задан: encrypt_value возвращает строку с префиксом "fernet:",
#     decrypt_value снимает префикс и расшифровывает.
#   - При расшифровке значения БЕЗ префикса "fernet:" (старая plaintext-запись
#     или значение, сохранённое без ключа) возвращается как есть — это даёт
#     автоматическую обратную совместимость и мягкую миграцию: старые записи
#     читаются, при следующем сохранении (если ключ задан) шифруются.
#   - При расшифровке значения С префиксом "fernet:" когда ключ НЕ задан —
#     значение нельзя прочитать, возвращается пустая строка + warning
#     (предотвращает передачу шифр-текста в подключение как пароля).
#
# Сгенерировать ключ:
#   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# =========================================================================

import os
import logging

logger = logging.getLogger(__name__)

_ENCRYPTION_PREFIX = "fernet:"

# Fernet-объект создаётся лениво (один раз) — чтобы не падать при импорте,
# если cryptography не установлен. _FERNET_AVAILABLE=False означает,
# что шифрование недоступно — все значения хранятся в открытом виде.
_FERNET = None
_FERNET_AVAILABLE = False
_FERNET_CHECKED = False


def _get_fernet():
    """Возвращает Fernet-объект или None. None = шифрование отключено
    (ключ не задан ИЛИ cryptography не установлен). Логирует warning
    один раз при первом вызове с заданным ключом, но недоступной lib."""
    global _FERNET, _FERNET_AVAILABLE, _FERNET_CHECKED
    if _FERNET_CHECKED:
        return _FERNET if _FERNET_AVAILABLE else None
    _FERNET_CHECKED = True

    key = os.getenv("CONNECTIONS_ENCRYPTION_KEY", "").strip()
    if not key:
        # Ключ не задан — шифрование выключено, это нормальный режим
        # для локальной разработки. Warning логирует stores при старте.
        return None

    try:
        from cryptography.fernet import Fernet
    except ImportError:
        logger.warning(
            "CONNECTIONS_ENCRYPTION_KEY задан, но пакет 'cryptography' не "
            "установлен. Секреты будут храниться в открытом виде. "
            "Установите: pip install cryptography"
        )
        return None

    try:
        _FERNET = Fernet(key.encode() if isinstance(key, str) else key)
        _FERNET_AVAILABLE = True
        return _FERNET
    except Exception as e:
        logger.error(
            "Не удалось инициализировать Fernet по CONNECTIONS_ENCRYPTION_KEY "
            "(%s). Секреты будут храниться в открытом виде. Ключ должен быть "
            "строкой base64, сгенерированной Fernet.generate_key().",
            e,
        )
        return None


def is_encryption_enabled() -> bool:
    """True, если шифрование активно (ключ задан И библиотека доступна)."""
    return _get_fernet() is not None


def encrypt_value(value: str) -> str:
    """Шифрует строку, если включено шифрование; иначе возвращает как есть.
    None/пустые значения возвращаются без изменений."""
    if not value:
        return value
    f = _get_fernet()
    if f is None:
        return value
    # Не шифровать повторно уже зашифрованное (защита от двойного шифрования
    # при повторном сохранении без изменений).
    if value.startswith(_ENCRYPTION_PREFIX):
        return value
    try:
        token = f.encrypt(value.encode("utf-8")).decode("ascii")
        return f"{_ENCRYPTION_PREFIX}{token}"
    except Exception as e:
        logger.warning("encrypt_value failed (%s); сохраняю в открытом виде.", e)
        return value


def decrypt_value(value: str) -> str:
    """Расшифровывает строку с префиксом 'fernet:'.
    Строки без префикса (plaintext/старые записи) возвращаются как есть.
    При невозможности расшифровать (ключ не задан, неверный ключ) —
    возвращает '' (чтобы не передать шифр-текст в подключение)."""
    if not value:
        return value
    if not value.startswith(_ENCRYPTION_PREFIX):
        return value
    f = _get_fernet()
    if f is None:
        logger.warning(
            "Зашифрованное значение найдено, но ключ CONNECTIONS_ENCRYPTION_KEY "
            "не задан/недоступен — значение не может быть прочитано."
        )
        return ""
    token = value[len(_ENCRYPTION_PREFIX):]
    try:
        return f.decrypt(token.encode("ascii")).decode("utf-8")
    except Exception as e:
        logger.warning("decrypt_value failed (%s); значение потеряно.", e)
        return ""
