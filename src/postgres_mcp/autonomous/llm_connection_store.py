# =========================================================================
# VERSION: 1.0.0
# Path: src/postgres_mcp/autonomous/llm_connection_store.py
# Реестр LLM-подключений (llm_connections.json).
#
# Структура одной записи:
#   {
#     "id": "a1b2c3d4",                # 8-символьный hex-идентификатор
#     "name": "My VseGPT",             # отображаемое имя (уникальное)
#     "mode": "cloud" | "local",       # режим (соответствует секции providers.yaml)
#     "provider": "vsegpt",            # идентификатор провайдера из providers.yaml
#     "connection_type": "openai_compatible",  # идентификатор типа подключения
#     "model": "gpt-4o-mini",          # выбранная модель
#     "params": {                      # параметры подключения (формат зависит от type)
#       "api_key": "fernet:...",       # секреты шифруются (см. crypto.py)
#       "base_url": "https://..."
#     },
#     "active": true                   # активное подключение (одно в реестре)
#   }
#
# По образцу connection_store.py: JSON-файл, load/save, выбор активного.
# Секретные поля (значения по ключам с secret:true в providers.yaml) при
# сохранении пропускаются через crypto.encrypt_value, при чтении —
# crypto.decrypt_value. Список секретных полей для каждого connection_type
# определяется в providers.yaml; здесь он передаётся явным параметром,
# чтобы избежать жёсткой связи с YAML (store остаётся независимым).
# =========================================================================

import os
import json
import logging
import uuid
from pathlib import Path

from .crypto import encrypt_value, decrypt_value, is_encryption_enabled

logger = logging.getLogger(__name__)


def get_llm_store_path() -> Path:
    """Путь к llm_connections.json. Зеркалирует логику connection_store:
    CONNECTIONS_STORE=global -> APPDATA/postgres-mcp/llm_connections.json,
    иначе -> ./llm_connections.json (рядом с запуском)."""
    mode = os.getenv("CONNECTIONS_STORE", "project").strip().lower()
    if mode == "global":
        base = Path(os.environ.get("APPDATA", Path.home())) / "postgres-mcp"
        base.mkdir(parents=True, exist_ok=True)
        return base / "llm_connections.json"
    return Path("llm_connections.json")


def _secret_fields(connection_type: str, providers_catalog: dict) -> set:
    """Возвращает множество имён полей, помеченных secret:true для данного
    connection_type в providers_catalog['connection_types']. Вызывается
    из app.py и передаётся сюда уже загруженный каталог."""
    ct_cfg = (providers_catalog or {}).get("connection_types", {}).get(connection_type, {})
    params = ct_cfg.get("params", {})
    return {name for name, meta in params.items() if meta.get("secret")}


def _apply_crypto_on_save(conn: dict, secret_fields: set) -> dict:
    """Возвращает копию записи с зашифрованными секретными полями.
    Не модифицирует исходный dict (безопасно для in-memory состояния)."""
    if not secret_fields:
        return conn
    out = dict(conn)
    params = dict(conn.get("params", {}))
    for field in secret_fields:
        if field in params:
            params[field] = encrypt_value(params[field])
    out["params"] = params
    return out


def _apply_crypto_on_load(conn: dict, secret_fields: set) -> dict:
    """Расшифровывает секретные поля в записи (in-place) сразу после чтения
    из файла. secret_fields — множество имён для текущего connection_type."""
    params = conn.get("params", {})
    for field in secret_fields:
        if field in params:
            params[field] = decrypt_value(params[field])
    return conn


def load_llm_connections(secret_fields_map: dict = None) -> list[dict]:
    """Загружает реестр подключений.
    secret_fields_map: {connection_type: {field1, field2, ...}} — для расшифровки.
        Если None — расшифровка не выполняется (возвращает зашифрованные значения)."""
    path = get_llm_store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
        conns = data if isinstance(data, list) else []
        # Нормализация обязательных полей
        for c in conns:
            c.setdefault("id", uuid.uuid4().hex[:8])
            c.setdefault("name", "unnamed")
            c.setdefault("mode", "cloud")
            c.setdefault("provider", "")
            c.setdefault("connection_type", "")
            c.setdefault("model", "")
            c.setdefault("params", {})
            c.setdefault("active", False)
            # Расшифровка секретов, если передана карта полей
            if secret_fields_map:
                sf = secret_fields_map.get(c.get("connection_type"), set())
                _apply_crypto_on_load(c, sf)
        return conns
    except Exception as e:
        logger.warning(f"Failed to load llm_connections: {e}")
        return []


def save_llm_connections(conns: list[dict], secret_fields_map: dict = None):
    """Сохраняет реестр. Секретные поля шифруются перед записью
    (in-memory записи остаются расшифрованными — шифр применяется к копиям)."""
    path = get_llm_store_path()
    to_write = []
    for c in conns:
        sf = (secret_fields_map or {}).get(c.get("connection_type"), set())
        to_write.append(_apply_crypto_on_save(c, sf))
    path.write_text(json.dumps(to_write, indent=2, ensure_ascii=False), "utf-8")


def get_active_llm_connection(secret_fields_map: dict = None) -> dict | None:
    """Возвращает активное подключение.
    Приоритет active-флага:
      1. Переменная ACTIVE_LLM_CONNECTION (.env) — сопоставление по name.
      2. active:true в записи.
    Первое совпадение выигрывает."""
    conns = load_llm_connections(secret_fields_map=secret_fields_map)
    if not conns:
        return None
    env_name = os.getenv("ACTIVE_LLM_CONNECTION", "").strip()
    if env_name:
        for c in conns:
            if c.get("name") == env_name:
                return c
    for c in conns:
        if c.get("active"):
            return c
    return conns[0] if conns else None


def set_active_llm_connection(conn_id: str, secret_fields_map: dict = None) -> list[dict]:
    """Отмечает подключение с данным id как активное (снимает флаг с остальных).
    Также очищает ACTIVE_LLM_CONNECTION в .env (чтобы не конфликтовать)."""
    conns = load_llm_connections(secret_fields_map=secret_fields_map)
    for c in conns:
        c["active"] = (c.get("id") == conn_id)
    save_llm_connections(conns, secret_fields_map=secret_fields_map)
    return conns


def add_llm_connection(conn: dict, make_active: bool = True, secret_fields_map: dict = None) -> list[dict]:
    """Добавляет новое подключение в реестр. Если make_active=True —
    делает его активным, сняв флаг с остальных."""
    conns = load_llm_connections(secret_fields_map=secret_fields_map)
    conn = dict(conn)
    conn.setdefault("id", uuid.uuid4().hex[:8])
    conn.setdefault("active", False)
    if make_active:
        for c in conns:
            c["active"] = False
        conn["active"] = True
    conns.append(conn)
    save_llm_connections(conns, secret_fields_map=secret_fields_map)
    return conns


def update_llm_connection(conn_id: str, updates: dict, secret_fields_map: dict = None) -> list[dict]:
    """Обновляет поля существующей записи по id.
    updates может содержать любые поля верхнего уровня (name, provider,
    connection_type, model) и params (сливается с существующими)."""
    conns = load_llm_connections(secret_fields_map=secret_fields_map)
    for c in conns:
        if c.get("id") == conn_id:
            for k, v in updates.items():
                if k == "params":
                    merged = dict(c.get("params", {}))
                    merged.update(v)
                    c["params"] = merged
                else:
                    c[k] = v
            break
    save_llm_connections(conns, secret_fields_map=secret_fields_map)
    return conns


def delete_llm_connection(conn_id: str, secret_fields_map: dict = None) -> list[dict]:
    """Удаляет подключение по id. Если удаляется активное — активным
    становится первое оставшееся (если есть)."""
    conns = load_llm_connections(secret_fields_map=secret_fields_map)
    removed = next((c for c in conns if c.get("id") == conn_id), None)
    conns = [c for c in conns if c.get("id") != conn_id]
    if removed and removed.get("active") and conns:
        conns[0]["active"] = True
    save_llm_connections(conns, secret_fields_map=secret_fields_map)
    return conns


def is_name_taken(name: str, exclude_id: str = None, secret_fields_map: dict = None) -> bool:
    conns = load_llm_connections(secret_fields_map=secret_fields_map)
    return any(c.get("name") == name and c.get("id") != exclude_id for c in conns)


def build_secret_fields_map(providers_catalog: dict) -> dict:
    """Собирает {connection_type: {secret_field_names}} из каталога providers.
    Удобная фабрика для передачи в load/save-функции."""
    result = {}
    for ct_name, ct_cfg in (providers_catalog or {}).get("connection_types", {}).items():
        params = ct_cfg.get("params", {})
        result[ct_name] = {name for name, meta in params.items() if meta.get("secret")}
    return result


def log_encryption_status():
    """Логирует статус шифрования при старте. Вызывается из app.py один раз."""
    if is_encryption_enabled():
        logger.info("LLM-connections encryption: ENABLED (Fernet). Секреты шифруются в llm_connections.json.")
    else:
        if os.getenv("CONNECTIONS_ENCRYPTION_KEY", "").strip():
            logger.warning("CONNECTIONS_ENCRYPTION_KEY задан, но шифрование недоступно (cryptography не установлен?).")
        else:
            logger.warning(
                "CONNECTIONS_ENCRYPTION_KEY не задан — секреты LLM-подключений "
                "хранятся в llm_connections.json в ОТКРЫТОМ виде. "
                "Сгенерируйте ключ: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )
