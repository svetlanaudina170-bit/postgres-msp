# =========================================================================
# VERSION: 1.2.0
# Path: src/postgres_mcp/autonomous/connection_store.py
# Изменения в 1.2.0:
#  - Поле "value" (URL БД с паролем) теперь шифруется перед записью и
#    расшифровывается после чтения через общий модуль crypto.py
#    (Fernet по CONNECTIONS_ENCRYPTION_KEY). Опционально (opt-in): если ключ
#    не задан — поведение прежнее, пароли в открытом виде (+ warning в логах).
#    Старые plaintext-записи читаются как есть; при следующем сохранении
#    с заданным ключом шифруются автоматически (мягкая миграция).
# =========================================================================

import os
import re
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from .crypto import encrypt_value, decrypt_value

logger = logging.getLogger(__name__)


def _mask_password(url: str) -> str:
    return re.sub(r"(://[^:]+:).+(@)", r"\1****\2", url)


def _make_key_from_url(url: str) -> str:
    p = _parse_url_parts(url)
    return f"{p['user']}@{p['host']}:{p['port']}/{p['database']}"


def _parse_url_parts(url: str) -> dict:
    parsed = urlparse(url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "postgres",
        "password": parsed.password or "",
        "database": parsed.path.lstrip("/") or "postgres",
    }


def get_store_path() -> Path:
    mode = os.getenv("CONNECTIONS_STORE", "project").strip().lower()
    if mode == "global":
        base = Path(os.environ.get("APPDATA", Path.home())) / "postgres-mcp"
        base.mkdir(parents=True, exist_ok=True)
        return base / "connections.json"
    return Path("connections.json")


def load_connections() -> list[dict]:
    path = get_store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text("utf-8"))
        conns = data if isinstance(data, list) else []
        for c in conns:
            if "url" in c:
                c["value"] = c.pop("url")
            if "label" in c:
                old_label = c.pop("label")
                c["key"] = old_label if old_label != c.get("value", old_label) else _make_key_from_url(c["value"])
            c.setdefault("key", _make_key_from_url(c.get("value", "")))
            c.setdefault("value", "")
            c.setdefault("default", False)
            # Расшифровка URL БД (включая пароль). Поля без префикса "fernet:"
            # (plaintext/старые записи) возвращаются как есть — обратная совместимость.
            c["value"] = decrypt_value(c["value"])
        conns.sort(key=lambda c: (not c.get("pinned", False), -(c.get("use_count", 0) or 0)))
        return conns
    except Exception as e:
        logger.warning(f"Failed to load connections: {e}")
        return []


def save_connections(conns: list[dict]):
    path = get_store_path()
    conns.sort(key=lambda c: (not c.get("pinned", False), -(c.get("use_count", 0) or 0)))
    # Шифруем поле value в КОПИЯХ записей — оригинальные dict в памяти
    # остаются с расшифрованными URL (чтобы UI/логика работали с реальными URL).
    to_write = []
    for c in conns:
        wc = dict(c)
        wc["value"] = encrypt_value(c.get("value", ""))
        to_write.append(wc)
    path.write_text(json.dumps(to_write, indent=2, ensure_ascii=False), "utf-8")


def build_choices(conns: list[dict] = None, show_value: bool = False) -> list[str]:
    if conns is None:
        conns = load_connections()
    result = []
    for c in conns:
        label = c["key"] if c["key"] else c["value"]
        if show_value and c["value"]:
            label = f"{c['key']} # {_mask_password(c['value'])}"
        if c.get("pinned"):
            label = "\U0001f4cc " + label
        result.append(label)
    return result


def parse_display(text: str, show_value: bool = False) -> tuple[str, str]:
    clean = text.removeprefix("\U0001f4cc ").strip()
    if show_value and " # " in clean:
        parts = clean.split(" # ", 1)
        return parts[0].strip(), parts[1].strip()
    return clean, ""


def find_by_key(key: str) -> dict | None:
    return next((c for c in load_connections() if c.get("key") == key), None)


def find_by_key_and_masked_value(key: str, masked_val: str = None, conns: list[dict] = None) -> dict | None:
    if conns is None:
        conns = load_connections()
    candidates = [c for c in conns if c.get("key") == key]
    if not masked_val or len(candidates) <= 1:
        return candidates[0] if candidates else None
    for c in candidates:
        if _mask_password(c.get("value", "")) == masked_val:
            return c
    return candidates[0] if candidates else None


def find_by_value(value: str) -> dict | None:
    return next((c for c in load_connections() if c.get("value") == value), None)


def find_by_id(conn_id: str) -> dict | None:
    return next((c for c in load_connections() if c.get("id") == conn_id), None)


def match_by_server(url: str, conns: list[dict] = None) -> dict | None:
    parts = _parse_url_parts(url)
    if conns is None:
        conns = load_connections()
    for c in conns:
        cp = _parse_url_parts(c["value"])
        if (cp["host"] == parts["host"] and cp["port"] == parts["port"]
                and cp["user"] == parts["user"]):
            return c
    return None


def match_existing(url: str, conns: list[dict] = None) -> dict | None:
    parts = _parse_url_parts(url)
    if conns is None:
        conns = load_connections()
    for c in conns:
        cp = _parse_url_parts(c["value"])
        if (cp["host"] == parts["host"] and cp["port"] == parts["port"]
                and cp["user"] == parts["user"] and cp["database"] == parts["database"]):
            return c
    return None


def add_connection(value: str, key: str = None, conns: list[dict] = None) -> list[dict]:
    if conns is None:
        conns = load_connections()
    conns.append({
        "id": uuid.uuid4().hex[:8],
        "key": (key or "").strip() or _make_key_from_url(value),
        "value": value,
        "pinned": False,
        "default": False,
        "use_count": 1,
        "last_used": datetime.now(timezone.utc).isoformat(),
    })
    save_connections(conns)
    return conns


def set_default(conn_id: str, conns: list[dict] = None) -> list[dict]:
    if conns is None:
        conns = load_connections()
    for c in conns:
        c["default"] = (c.get("id") == conn_id)
    save_connections(conns)
    return conns


def get_default(conns: list[dict] = None) -> dict | None:
    if conns is None:
        conns = load_connections()
    return next((c for c in conns if c.get("default")), None)


def is_key_taken(key: str, exclude_id: str = None, conns: list[dict] = None) -> bool:
    if conns is None:
        conns = load_connections()
    return any(c.get("key") == key and c.get("id") != exclude_id for c in conns)


def update_connection(conn_id: str, value: str, key: str = None, conns: list[dict] = None) -> list[dict]:
    if conns is None:
        conns = load_connections()
    for c in conns:
        if c["id"] == conn_id:
            c["value"] = value
            if key:
                c["key"] = key
            c["use_count"] = (c.get("use_count", 0) or 0) + 1
            c["last_used"] = datetime.now(timezone.utc).isoformat()
            break
    save_connections(conns)
    return conns


def update_key(conn_id: str, new_key: str, conns: list[dict] = None) -> list[dict]:
    if conns is None:
        conns = load_connections()
    for c in conns:
        if c["id"] == conn_id:
            c["key"] = new_key
            break
    save_connections(conns)
    return conns


def delete_connection(conn_id: str, conns: list[dict] = None) -> list[dict]:
    if conns is None:
        conns = load_connections()
    conns = [c for c in conns if c["id"] != conn_id]
    save_connections(conns)
    return conns


def toggle_pin(conn_id: str, conns: list[dict] = None) -> list[dict]:
    if conns is None:
        conns = load_connections()
    for c in conns:
        if c["id"] == conn_id:
            c["pinned"] = not c.get("pinned", False)
            break
    save_connections(conns)
    return conns


def bump_usage(conn_id: str, conns: list[dict] = None) -> list[dict]:
    if conns is None:
        conns = load_connections()
    for c in conns:
        if c["id"] == conn_id:
            c["use_count"] = (c.get("use_count", 0) or 0) + 1
            c["last_used"] = datetime.now(timezone.utc).isoformat()
            break
    save_connections(conns)
    return conns
