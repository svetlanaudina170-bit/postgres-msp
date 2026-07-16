# =========================================================================
# VERSION: 1.5.0
# Path: src/postgres_mcp/autonomous/app.py
# Изменения в 1.5.0 (РЕАЛИЗАЦИЯ 4-уровневой иерархии LLM-подключений):
#  - ВНИМАНИЕ: заголовок v1.4.0 в предыдущей версии ОПИСЫВАЛ эту фичу, но
#    код её не реализовывал (импорты build_llm_client_from_connection /
#    llm_conn_store были, но нигде не использовались; вкладки Chat/LLM
#    Settings оставались старыми). Эта версия — первая, где фича реально
#    работает end-to-end.
#  - ВКЛАДКА LLM SETTINGS ПЕРЕРАБОТАНА: вместо плоской формы
#    (Provider + Model + API Key + Base URL) теперь реестр подключений
#    (llm_connections.json) + модальное окно с 4-уровневым селектором:
#       Mode (Cloud/Local)
#         -> Provider (OpenAI, Anthropic, VseGPT, Yandex, Ollama, LM Studio, ...)
#            -> Connection Type (OpenAI-compatible / Anthropic native / Google / Yandex)
#               -> Model
#    Параметры подключения (api_key, base_url, folder_id, anthropic_version)
#    показываются/скрываются автоматически в зависимости от выбранного
#    Connection Type (определяется каталогом config/providers.yaml). Кнопка
#    "Fetch models" живо запрашивает /v1/models у провайдера, если это
#    поддерживается (openai_compatible).
#  - НОВАЯ ВКЛАДКА CHAT: больше не имеет своих дропдаунов Mode/Provider/Model
#    (они убраны как источник истины). Чат показывает активное подключение
#    (readonly: имя + модель) + кнопку "Edit", открывающую то же модальное
#    окно, что и на вкладке LLM Settings. Источник истины теперь один — реестр.
#  - chat_fn() / build_llm_client() переписаны: читают активное подключение
#    из llm_connections.json -> build_llm_client_from_connection(). При
#    пустом реестре fallback на старый get_llm_client() из плоских .env-переменных.
#  - Шифрование секретов: LLM-ключи в llm_connections.json шифруются Fernet
#    по CONNECTIONS_ENCRYPTION_KEY (общий модуль crypto.py).
#  - gr.Modal объявлен ВНЕ gr.Tabs() — обход известного бага Gradio, когда
#    модал visible=False под TabItem "показывается" при повторном входе на
#    вкладку.
#  - Убран хардкод ["openai","anthropic","google"] в обработчиках чата
#    (стр.593-594 в v1.3.0) — список провайдеров берётся из providers.yaml.
#  - Лимит итераций tool-calling цикла вынесен в .env CHAT_MAX_TOOL_ITERATIONS
#    (было захардкожено range(5)).
# Изменения в 1.4.0 (только заголовок, код не менялся — см. выше):
# Изменения в 1.3.0:
#  - ИСПРАВЛЕНО "зависание" при переключении Mode (Remote/Local) на
#    вкладке Chat: обработчики использовали queue=False.
# Изменения в 1.2.0:
#  - ИСПРАВЛЕН функциональный баг: chat_fn() теперь передаёт temperature
#    и max_tokens (читаются из .env LLM_TEMPERATURE/LLM_MAX_TOKENS на
#    каждый вызов) в LLMClient.chat().
# Изменения в 1.1.0:
#  - Добавлен import yaml, загрузка config/ui_settings.yaml и
#    config/prompts.yaml при старте (с безопасными fallback-значениями).
# =========================================================================
#!/usr/bin/env python3
import os

# Ensure localhost requests bypass system proxy (fixes Gradio API 502 on
# Windows systems with a system proxy like Clash/V2Ray on 127.0.0.1).
_no_proxy = os.environ.get("NO_PROXY", "")
if "127.0.0.1" not in _no_proxy:
    os.environ["NO_PROXY"] = (_no_proxy + ",127.0.0.1,localhost").lstrip(",")
    os.environ["no_proxy"] = os.environ["NO_PROXY"]

import json
import logging

import gradio as gr
import yaml
from dotenv import load_dotenv

from . import llm_connection_store as llm_conn_store
from .connection_store import _make_key_from_url
from .connection_store import _mask_password
from .connection_store import _parse_url_parts
from .connection_store import add_connection
from .connection_store import build_choices
from .connection_store import bump_usage
from .connection_store import delete_connection
from .connection_store import find_by_key
from .connection_store import find_by_key_and_masked_value
from .connection_store import find_by_value
from .connection_store import get_default
from .connection_store import is_key_taken
from .connection_store import load_connections
from .connection_store import match_by_server
from .connection_store import match_existing
from .connection_store import parse_display
from .connection_store import set_default
from .connection_store import toggle_pin
from .connection_store import update_key
from .connection_store import update_connection
from .connection_store import get_all_tags
from .connection_store import set_connection_tags
from .llm_client import LLMClient
from .llm_client import LLMResponse
from .llm_client import build_llm_client_from_connection
from .llm_client import get_llm_client
from .pg_client import PostgresClient
from ..sql_editor import SQLBuilder, stmt_type_choices, template_names, apply_template, get_template_by_name, get_history

# Compute ENV_PATH before load_dotenv so it finds .env regardless of CWD
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
ENV_PATH = os.path.join(_PROJECT_ROOT, ".env")
CONFIG_DIR = os.path.join(_PROJECT_ROOT, "config")
UI_SETTINGS_PATH = os.path.join(CONFIG_DIR, "ui_settings.yaml")
PROMPTS_PATH = os.path.join(CONFIG_DIR, "prompts.yaml")
PROVIDERS_PATH = os.path.join(CONFIG_DIR, "providers.yaml")

load_dotenv(ENV_PATH)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("pg_mcp")

APP_TITLE = os.getenv("APP_TITLE", "PostgreSQL MCP Autonomous")
APP_HOST = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT = int(os.getenv("APP_PORT", "7862"))
THEME = os.getenv("THEME", "default")
SHOW_VAL = os.getenv("SHOW_VALUE", "false").lower() == "true"

# --- Операционные лимиты (см. .env для описаний) ---
CHAT_MAX_TOOL_ITERATIONS = int(os.getenv("CHAT_MAX_TOOL_ITERATIONS", "5"))
CHAT_TOOL_RESULT_TRUNCATE = int(os.getenv("CHAT_TOOL_RESULT_TRUNCATE", "1000"))
SQL_MAX_ROWS_DISPLAY = int(os.getenv("SQL_MAX_ROWS_DISPLAY", "100"))
LLM_TEMP_MIN = float(os.getenv("LLM_TEMP_MIN", "0"))
LLM_TEMP_MAX = float(os.getenv("LLM_TEMP_MAX", "2"))
LLM_TEMP_STEP = float(os.getenv("LLM_TEMP_STEP", "0.05"))
LLM_MAXTOKENS_MIN = int(os.getenv("LLM_MAXTOKENS_MIN", "1"))
LLM_MAXTOKENS_MAX = int(os.getenv("LLM_MAXTOKENS_MAX", "100000"))
LLM_FETCH_MODELS_TIMEOUT = float(os.getenv("LLM_FETCH_MODELS_TIMEOUT", "10"))

BLOCKS_CSS = """
    .saved-dd .wrap-inner { flex-wrap: nowrap !important; }
    .saved-dd .secondary-wrap { min-width: 0 !important; }
    .saved-dd input {
      padding-right: 28px !important;
      overflow: hidden !important;
      text-overflow: ellipsis !important;
      white-space: nowrap !important;
      min-width: 0 !important;
      cursor: pointer !important;
    }
    .saved-dd .icon-wrap {
      flex-shrink: 0 !important;
      pointer-events: none !important;
    }
    #llm-modal {
      position: fixed;
      top: 50%;
      left: 50%;
      transform: translate(-50%, -50%);
      z-index: 9999;
      border: 2px solid var(--border-color-primary, #888);
      border-radius: 12px;
      padding: 20px;
      background: var(--background-fill-primary, #fff);
      box-shadow: 0 8px 32px rgba(0,0,0,0.3);
      max-width: 800px;
      width: 90%;
      max-height: 85vh;
      overflow-y: auto;
    }
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0,0,0,0.4);
      z-index: 9998;
    }
    #status_display textarea {
      max-height: 200px;
      overflow-y: auto !important;
      resize: vertical;
    }
"""


# --- Fallback-значения на случай отсутствия/повреждения yaml-файлов ---
_UI_SETTINGS_FALLBACK = {
    "connection_tab": {
        "saved_connections": {"label": "Saved Connections", "readonly": False},
        "database": {"label": "Database", "readonly": True},
    },
    "chat_tab": {
        "mode": {"label": "Mode", "choices": ["remote", "local"]},
        "provider": {"label": "Provider", "readonly": True, "choices": ["openai", "anthropic", "google", "local"]},
        "model": {
            "label": "Model",
            "readonly": False,
            "models_by_provider": {
                "openai": ["gpt-4o-mini", "gpt-4o", "gpt-4o-turbo", "gpt-4", "gpt-4-turbo", "gpt-3.5-turbo", "o1-mini", "o1-preview", "o3-mini"],
                "anthropic": [
                    "claude-3-5-sonnet-latest",
                    "claude-3-5-haiku-latest",
                    "claude-3-opus-latest",
                    "claude-3-haiku-latest",
                    "claude-2",
                    "claude-instant-1.2",
                ],
                "google": ["gemini-2.0-flash", "gemini-2.0-pro-exp", "gemini-1.5-flash", "gemini-1.5-pro", "gemini-1.5-pro-001"],
                "local": ["local-model"],
            },
        },
    },
    "llm_settings_tab": {
        "provider": {"label": "Provider", "readonly": True, "choices": ["openai", "anthropic", "google"]},
    },
}

_PROMPTS_FALLBACK = {
    "system_prompt": """You are a PostgreSQL database assistant. Help users explore databases, write SQL, and analyze performance.

Tools:
- execute_sql(sql) — run any SQL query
- get_schema() — get full database schema
- explain_query(sql) — get execution plan
- get_health_report() — database health overview

Rules:
1. Explain SQL before executing
2. Use SELECT for exploration, never DELETE/DROP without explicit request
3. Show schema context when relevant
4. Format results as readable tables"""
}

# Минимальный fallback-каталог провайдеров (на случай отсутствия/повреждения
# providers.yaml). Структура та же, что и в config/providers.yaml.
_PROVIDERS_FALLBACK = {
    "connection_types": {
        "openai_compatible": {
            "label": "OpenAI-compatible (chat/completions)",
            "llm_method": "openai",
            "params": {
                "api_key": {"label": "API Key", "required": False, "secret": True, "placeholder": "sk-..."},
                "base_url": {"label": "Base URL", "required": True, "secret": False, "placeholder": "https://api.openai.com/v1"},
            },
            "models_endpoint": "/models",
            "models_endpoint_format": "openai",
        },
        "anthropic_native": {
            "label": "Anthropic native (messages)",
            "llm_method": "anthropic",
            "params": {
                "api_key": {"label": "API Key", "required": True, "secret": True, "placeholder": "sk-ant-..."},
                "base_url": {
                    "label": "Base URL",
                    "required": False,
                    "secret": False,
                    "default": "https://api.anthropic.com/v1",
                    "placeholder": "https://api.anthropic.com/v1",
                },
                "anthropic_version": {
                    "label": "anthropic-version",
                    "required": True,
                    "secret": False,
                    "default": "2023-06-01",
                    "placeholder": "2023-06-01",
                },
            },
            "models_endpoint": None,
        },
        "google_native": {
            "label": "Google AI (generateContent)",
            "llm_method": "google",
            "params": {
                "api_key": {"label": "API Key", "required": True, "secret": True, "placeholder": "AIza..."},
                "base_url": {
                    "label": "Base URL",
                    "required": False,
                    "secret": False,
                    "default": "https://generativelanguage.googleapis.com/v1beta",
                    "placeholder": "https://generativelanguage.googleapis.com/v1beta",
                },
            },
            "models_endpoint": None,
        },
    },
    "cloud": {
        "enabled": True,
        "providers": {
            "openai": {
                "enabled": True,
                "label": "OpenAI",
                "connection_types": ["openai_compatible"],
                "models": {"openai_compatible": _UI_SETTINGS_FALLBACK["chat_tab"]["model"]["models_by_provider"]["openai"]},
            },
            "anthropic": {
                "enabled": True,
                "label": "Anthropic",
                "connection_types": ["anthropic_native"],
                "models": {"anthropic_native": _UI_SETTINGS_FALLBACK["chat_tab"]["model"]["models_by_provider"]["anthropic"]},
            },
            "google": {
                "enabled": True,
                "label": "Google",
                "connection_types": ["google_native"],
                "models": {"google_native": _UI_SETTINGS_FALLBACK["chat_tab"]["model"]["models_by_provider"]["google"]},
            },
        },
    },
    "local": {
        "enabled": True,
        "providers": {
            "lmstudio": {"enabled": True, "label": "LM Studio", "connection_types": ["openai_compatible"], "models": {"openai_compatible": []}},
        },
    },
}


def _load_yaml(path: str, fallback: dict) -> dict:
    if not os.path.exists(path):
        logger.warning(f"Config file not found: {path}. Using built-in fallback.")
        return fallback
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data if data else fallback
    except Exception as e:
        logger.warning(f"Failed to load {path}: {e}. Using built-in fallback.")
        return fallback


UI_SETTINGS = _load_yaml(UI_SETTINGS_PATH, _UI_SETTINGS_FALLBACK)
PROMPTS = _load_yaml(PROMPTS_PATH, _PROMPTS_FALLBACK)
PROVIDERS = _load_yaml(PROVIDERS_PATH, _PROVIDERS_FALLBACK)

# Карта секретных полей по connection_type — для шифрования при save/load реестра.
SECRET_FIELDS_MAP = llm_conn_store.build_secret_fields_map(PROVIDERS)


def _dd_cfg(section: str, key: str) -> dict:
    return UI_SETTINGS.get(section, {}).get(key, {})


def get_system_prompt() -> str:
    env_override = os.getenv("LLM_SYSTEM_PROMPT", "").strip()
    if env_override:
        return env_override
    return PROMPTS.get("system_prompt", _PROMPTS_FALLBACK["system_prompt"])


# ----------------------------------------------------------------------------
# Catalog helpers: навигация по providers.yaml (mode -> provider -> conn_type -> models)
# ----------------------------------------------------------------------------


def _section_enabled(section: str) -> bool:
    """Видимость секции cloud/local с учётом .env-переопределений.
    Пустое значение в .env = использовать enabled из YAML."""
    env_flag = os.getenv(f"LLM_{section.upper()}_ENABLED", "").strip().lower()
    if env_flag in ("true", "false"):
        return env_flag == "true"
    return bool(PROVIDERS.get(section, {}).get("enabled", True))


def _providers_in_section(section: str) -> list[tuple[str, dict]]:
    """Список (provider_id, provider_cfg) с enabled=true в секции."""
    if not _section_enabled(section):
        return []
    providers = PROVIDERS.get(section, {}).get("providers", {})
    return [(pid, pcfg) for pid, pcfg in providers.items() if pcfg.get("enabled", True)]


def _mode_choices() -> list[str]:
    """Доступные режимы (cloud/local) для дропдауна Mode."""
    modes = []
    if _section_enabled("cloud"):
        modes.append("cloud")
    if _section_enabled("local"):
        modes.append("local")
    return modes or ["cloud"]


def _provider_choices(mode: str) -> list[tuple[str, str]]:
    """Список (provider_id, label) для выбранного mode."""
    return [(pid, pcfg.get("label", pid)) for pid, pcfg in _providers_in_section(mode)]


def _conn_type_choices(provider_id: str, mode: str) -> list[tuple[str, str]]:
    """Список (conn_type_id, label) для выбранного провайдера."""
    providers = PROVIDERS.get(mode, {}).get("providers", {})
    pcfg = providers.get(provider_id, {})
    ct_catalog = PROVIDERS.get("connection_types", {})
    result = []
    for ct_id in pcfg.get("connection_types", []):
        ct_cfg = ct_catalog.get(ct_id, {})
        result.append((ct_id, ct_cfg.get("label", ct_id)))
    return result


def _model_choices(provider_id: str, mode: str, conn_type: str) -> list[str]:
    """Список моделей для (provider, conn_type) из каталога (без live-fetch)."""
    providers = PROVIDERS.get(mode, {}).get("providers", {})
    pcfg = providers.get(provider_id, {})
    models = pcfg.get("models", {}).get(conn_type, [])
    return list(models) if models else []


def _conn_type_cfg(conn_type: str) -> dict:
    return PROVIDERS.get("connection_types", {}).get(conn_type, {})


def _param_fields_for_ct(conn_type: str) -> list[str]:
    """Упорядоченный список имён параметров для connection_type."""
    params = _conn_type_cfg(conn_type).get("params", {})
    return list(params.keys())


def _param_meta(conn_type: str, field: str) -> dict:
    return _conn_type_cfg(conn_type).get("params", {}).get(field, {})


def _param_default(conn_type: str, field: str, provider_id: str = "", mode: str = "") -> str:
    """Дефолтное значение параметра: сначала params_override провайдера, потом default connection_type."""
    # params_override на уровне провайдера
    if provider_id and mode:
        providers = PROVIDERS.get(mode, {}).get("providers", {})
        override = providers.get(provider_id, {}).get("params_override", {}).get(conn_type, {}).get(field, {})
        if "default" in override:
            return override["default"]
    return _param_meta(conn_type, field).get("default", "")


def _models_endpoint(conn_type: str) -> str | None:
    return _conn_type_cfg(conn_type).get("models_endpoint")


def _format_active_connection(conn: dict | None) -> str:
    """Строка для readonly-показа активного подключения в Chat/LLM Settings."""
    if not conn:
        env_fallback = os.getenv("LLM_PROVIDER", "")
        env_model = os.getenv("LLM_MODEL", "")
        if env_fallback or env_model:
            return f"_Активное: **{env_fallback}** / {env_model} (из .env fallback — реестр пуст)_"
        return "_Нет активного LLM-подключения. Создайте его на вкладке LLM Settings._"
    name = conn.get("name", "?")
    model = conn.get("model", "?")
    mode = conn.get("mode", "?")
    provider_label = PROVIDERS.get(mode, {}).get("providers", {}).get(conn.get("provider", ""), {}).get("label", conn.get("provider", "?"))
    ct_label = _conn_type_cfg(conn.get("connection_type", "")).get("label", conn.get("connection_type", "?"))
    return f"Активное: **{name}** — {provider_label} ({ct_label}) / модель: `{model}`"


def _llm_conn_choices() -> list[str]:
    """Список имён подключений для дропдауна реестра (активное первым)."""
    conns = llm_conn_store.load_llm_connections(secret_fields_map=SECRET_FIELDS_MAP)
    if not conns:
        return []
    # активное первым
    active = llm_conn_store.get_active_llm_connection(secret_fields_map=SECRET_FIELDS_MAP)
    active_name = active.get("name", "") if active else ""
    names = [c.get("name", "?") for c in conns]
    if active_name and active_name in names:
        names.remove(active_name)
        names.insert(0, active_name)
    return names


def save_env_file(updates: dict[str, str]) -> None:
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            lines = f.readlines()
    updated = set()
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "=" in stripped and not stripped.startswith("#"):
            key = stripped.split("=", 1)[0].strip()
            if key in updates:
                lines[i] = f"{key}={updates[key]}\n"
                updated.add(key)
    for key, val in updates.items():
        if key not in updated:
            lines.append(f"{key}={val}\n")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    load_dotenv(override=True)


pg = PostgresClient()

POSTGRES_TOOLS = [
    {
        "name": "execute_sql",
        "description": "Execute a SQL query",
        "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
    },
    {"name": "get_schema", "description": "Get full database schema", "parameters": {"type": "object", "properties": {}}},
    {
        "name": "explain_query",
        "description": "Get execution plan",
        "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
    },
    {"name": "get_health_report", "description": "Database health overview", "parameters": {"type": "object", "properties": {}}},
]


async def handle_tool(name: str, args: dict) -> str:
    if name == "execute_sql":
        r = await pg.execute_sql(args.get("sql", ""))
        if r.error:
            return f"Error: {r.error}"
        if not r.columns:
            return f"OK. {r.row_count} rows affected"
        return json.dumps([dict(zip(r.columns, row)) for row in r.rows], indent=2, default=str)
    if name == "get_schema":
        return await pg.get_schema_text()
    if name == "explain_query":
        return await pg.explain_query(args.get("sql", ""))
    if name == "get_health_report":
        return await pg.get_health_report()
    return f"Unknown tool: {name}"


def build_llm_client() -> "LLMClient":
    """Собирает LLMClient из АКТИВНОГО подключения реестра.
    При пустом реестре — fallback на старый get_llm_client() из плоских .env."""
    conn = llm_conn_store.get_active_llm_connection(secret_fields_map=SECRET_FIELDS_MAP)
    if conn:
        return build_llm_client_from_connection(conn, PROVIDERS)
    logger.info("LLM registry empty — using .env fallback (get_llm_client).")
    return get_llm_client()


async def chat_fn(message: str, history: list) -> tuple[str, list]:
    if not pg.is_connected:
        return "", history + [{"role": "assistant", "content": "Not connected to any database. Please connect first."}]
    llm = build_llm_client()
    # Читаем temperature/max_tokens из .env на каждый вызов (не константой
    # на уровне модуля), чтобы значения, сохранённые кнопкой "Save All to
    # .env" на вкладке LLM Settings, применялись сразу — без перезапуска.
    chat_temperature = float(os.getenv("LLM_TEMPERATURE", "0.3"))
    chat_max_tokens = int(os.getenv("LLM_MAX_TOKENS", "2000"))
    msgs = [{"role": h["role"], "content": h["content"]} for h in history]
    msgs.append({"role": "user", "content": message})
    history = history + [{"role": "user", "content": message}]
    full = ""
    for _ in range(CHAT_MAX_TOOL_ITERATIONS):
        resp: LLMResponse = await llm.chat(
            msgs,
            get_system_prompt(),
            POSTGRES_TOOLS,
            temperature=chat_temperature,
            max_tokens=chat_max_tokens,
        )
        if not resp.tool_calls:
            safe = (full + (resp.content or "")).replace("![", "[")
            new_history = history + [{"role": "assistant", "content": safe}]
            return "", new_history
        msgs.append(
            {
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": [{"id": t["id"], "name": t["name"], "arguments": t["arguments"]} for t in resp.tool_calls],
            }
        )
        for tc in resp.tool_calls:
            text = await handle_tool(tc["name"], tc["arguments"])
            msgs.append({"role": "tool", "tool_call_id": tc["id"], "content": text})
            if resp.content:
                full += resp.content + "\n"
            full += f"\U0001f527 Used: `{tc['name']}`\n```\n{text[:CHAT_TOOL_RESULT_TRUNCATE]}\n```\n"
    return "", history + [{"role": "assistant", "content": full + "\n\n_Max iterations reached._"}]


# --- Connection tab handlers (без изменений относительно v1.2.0) ---


def _rebuild_saved_dd(conns: list[dict] = None, show_value: bool = False, value: str = None) -> dict:
    if conns is None:
        conns = load_connections()
    kwargs = dict(choices=build_choices(conns, show_value=show_value))
    if value is not None:
        kwargs["value"] = value
    return gr.update(**kwargs)


def _rebuild_saved_dd_filtered(tag: str, show_value: bool = False) -> dict:
    """Rebuild saved connections dropdown filtered by tag."""
    conns = load_connections()
    if tag and tag != "All":
        conns = [c for c in conns if tag in c.get("tags", [])]
    kwargs = dict(choices=build_choices(conns, show_value=show_value))
    return gr.update(**kwargs)


def handle_tag_filter(tag: str, show_val: bool) -> dict:
    """Filter connections by tag."""
    return _rebuild_saved_dd_filtered(tag, show_value=show_val)


def _load_ui_settings() -> dict:
    """Load UI settings from .env."""
    return {
        "show_tag_filter": os.getenv("UI_SHOW_TAG_FILTER", "true").lower() == "true",
        "show_track_changes": os.getenv("UI_SHOW_TRACK_CHANGES", "true").lower() == "true",
        "show_connection_actions": os.getenv("UI_SHOW_CONNECTION_ACTIONS", "true").lower() == "true",
        "show_status_section": os.getenv("UI_SHOW_STATUS_SECTION", "true").lower() == "true",
    }


def handle_save_ui_settings(show_tag_filter: bool, show_track_changes: bool, show_connection_actions: bool, show_status_section: bool) -> str:
    """Save UI settings to .env."""
    save_env_file({
        "UI_SHOW_TAG_FILTER": str(show_tag_filter).lower(),
        "UI_SHOW_TRACK_CHANGES": str(show_track_changes).lower(),
        "UI_SHOW_CONNECTION_ACTIONS": str(show_connection_actions).lower(),
        "UI_SHOW_STATUS_SECTION": str(show_status_section).lower(),
    })
    return "\u2705 Settings saved"


def handle_toggle_settings(show: bool) -> dict:
    """Toggle settings panel visibility."""
    return gr.update(visible=show)


def _apply_ui_settings(settings: dict) -> tuple:
    """Apply UI settings to components visibility."""
    return (
        gr.update(visible=settings["show_tag_filter"]),
        gr.update(visible=settings["show_track_changes"]),
        gr.update(visible=settings["show_connection_actions"]),
        gr.update(visible=settings["show_status_section"]),
    )


def _build_label(conn: dict, show_val: bool) -> str:
    label = conn.get("key", "")
    if conn.get("pinned"):
        label = "\U0001f4cc " + label
    if show_val and conn.get("value"):
        label = f"{label} # {_mask_password(conn['value'])}"
    return label


def _conn_btns(connected: bool) -> tuple[dict, dict, dict]:
    if connected:
        return (
            gr.update(value="\U0001f504 ReConnect", variant="primary"),
            gr.update(value="\U0001f504 ReDiscover Databases", variant="secondary"),
            gr.update(value="\u2716 Disconnect", variant="stop", visible=True),
        )
    return (
        gr.update(value="Connect", variant="primary"),
        gr.update(value="\U0001f50d Discover Databases", variant="secondary"),
        gr.update(value="\u2716 Disconnect", variant="stop", visible=False),
    )


async def handle_discover(url: str) -> tuple[str, dict]:
    if not url.strip():
        return "Enter a URL to discover", gr.update(choices=[])
    parts = _parse_url_parts(url)
    current_db = parts.get("database", "postgres")
    sys_url = f"postgresql://{parts['user']}:{parts['password']}@{parts['host']}:{parts['port']}/postgres"
    dbs = await pg.get_databases(sys_url)
    if not dbs or dbs[0].get("error"):
        dbs = await pg.get_databases(url.strip())
    if not dbs or dbs[0].get("error"):
        return (f"\u274c {dbs[0]['error']}" if dbs else "No databases"), gr.update(choices=[], value=None)
    db_names = [d["name"] for d in dbs]
    # Add checkmark to currently selected database
    choices_with_check = []
    for name in db_names:
        if name == current_db:
            choices_with_check.append(f"\u2705 {name}")
        else:
            choices_with_check.append(name)
    # Set value to checked version if current_db exists
    selected = f"\u2705 {current_db}" if current_db in db_names else None
    return (
        f"\U0001f4e6 Found {len(dbs)} databases:\n" + "\n".join(f"  \u2022 `{d['name']}`" for d in dbs),
        gr.update(choices=choices_with_check, value=selected)
    )


def handle_db_select(db_name: str, current_url: str) -> str:
    if not db_name:
        return current_url
    # Strip checkmark prefix if present
    if db_name.startswith("\u2705 "):
        db_name = db_name[2:]
    idx = current_url.rfind("/")
    if idx > 8:
        return current_url[: idx + 1] + db_name
    return current_url


async def handle_connect(url: str, selected_db: str) -> tuple[str, dict, dict, dict]:
    if not url.strip():
        return "Enter a URL", *_conn_btns(pg.is_connected)
    target = url.strip()
    idx = target.rfind("/")
    if idx <= 8 and selected_db:
        target = f"{target.rstrip('/')}/{selected_db}"
    if pg.is_connected:
        await pg.disconnect()
    err = await pg.connect(target)
    if err:
        return f"\u274c {err}", *_conn_btns(False)
    r = await pg.execute_sql("SELECT version()")
    text = f"\u2705 Connected\n{r.rows[0][0] if r.rows else ''}" if not r.error else f"\u274c {r.error}"
    return text, *_conn_btns(True)


async def handle_disconnect() -> tuple[str, dict, dict, dict]:
    await pg.disconnect()
    return "Disconnected", *_conn_btns(False)


def handle_conn_select(display: str, current_url: str) -> tuple[str, str, str]:
    if not display:
        return current_url, _make_key_from_url(current_url), ""
    key, _ = parse_display(display, show_value=True)
    conn = find_by_key(key)
    if conn:
        tags = conn.get("tags", [])
        return conn["value"], conn.get("key", conn["value"]), ", ".join(tags)
    return current_url, _make_key_from_url(current_url), ""


def handle_save_url(url: str, key: str) -> tuple[dict, str]:
    if not url.strip():
        return gr.update(), "Enter a URL to save"
    key = key.strip() or _make_key_from_url(url)
    conns = load_connections()
    if is_key_taken(key, conns=conns):
        existing = find_by_key(key)
        if existing and existing.get("value") != url:
            return gr.update(), f"\u26a0\ufe0f Name '{key}' already used. Choose a different name."
    if match_existing(url, conns):
        conns = bump_usage(match_existing(url, conns)["id"], conns)
        return _rebuild_saved_dd(conns), "\u2705 Usage updated"
    if match_by_server(url, conns):
        conns = add_connection(url, key, conns)
        return _rebuild_saved_dd(conns), "\u2705 Saved as new (different database)"
    conns = add_connection(url, key, conns)
    return _rebuild_saved_dd(conns), "\u2705 Connection saved"


def _find_from_dd(display: str, show_val: bool):
    if not display:
        return None
    key, val = parse_display(display, show_value=show_val)
    if show_val and val:
        return find_by_key_and_masked_value(key, val)
    return find_by_key(key) or (find_by_value(val) if val else None)


def handle_pin_toggle(display: str, show_val: bool) -> dict:
    conn = _find_from_dd(display, show_val)
    if conn:
        conns = toggle_pin(conn["id"])
        updated = next((c for c in conns if c["id"] == conn["id"]), conn)
        return _rebuild_saved_dd(conns, show_value=show_val, value=_build_label(updated, show_val))
    return gr.update()


def handle_rename(display: str, new_key: str, show_val: bool) -> tuple[dict, str]:
    if not display or not new_key:
        return gr.update(), ""
    conn = _find_from_dd(display, show_val)
    if not conn:
        return gr.update(), ""
    if is_key_taken(new_key, exclude_id=conn["id"]):
        return gr.update(), f"\u26a0\ufe0f Name '{new_key}' already used. Choose a different name."
    conns = update_key(conn["id"], new_key)
    conn["key"] = new_key
    return gr.update(choices=build_choices(conns, show_value=show_val), value=_build_label(conn, show_val)), f"\u2705 Renamed to '{new_key}'"


def handle_edit(display: str, show_val: bool) -> tuple[str, str, str, str]:
    """Load selected connection into form for editing."""
    conn = _find_from_dd(display, show_val)
    if not conn:
        return "", "", "", ""
    tags = conn.get("tags", [])
    return conn.get("value", ""), conn.get("key", ""), conn.get("id", ""), ", ".join(tags)


def handle_save_url_edit(url: str, key: str, editing_id: str, tags_str: str) -> tuple[dict, str, str]:
    """Save or update a connection."""
    if not url.strip():
        return gr.update(), "Enter a URL to save", ""
    key = key.strip() or _make_key_from_url(url)
    tags = [t.strip() for t in (tags_str or "").split(",") if t.strip()]
    conns = load_connections()

    if editing_id:
        # Update existing connection
        conns = update_connection(editing_id, url, key, conns)
        conns = set_connection_tags(editing_id, tags, conns)
        return _rebuild_saved_dd(conns), f"\u2705 Updated '{key}'", ""

    # New connection logic
    if is_key_taken(key, conns=conns):
        existing = find_by_key(key)
        if existing and existing.get("value") != url:
            return gr.update(), f"\u26a0\ufe0f Name '{key}' already used. Choose a different name.", ""
    if match_existing(url, conns):
        conns = bump_usage(match_existing(url, conns)["id"], conns)
        return _rebuild_saved_dd(conns), "\u2705 Usage updated", ""
    if match_by_server(url, conns):
        conns = add_connection(url, key, conns)
        # Set tags on the newly added connection
        new_conn = find_by_key(key)
        if new_conn:
            conns = set_connection_tags(new_conn["id"], tags, conns)
        return _rebuild_saved_dd(conns), "\u2705 Saved as new (different database)", ""
    conns = add_connection(url, key, conns)
    new_conn = find_by_key(key)
    if new_conn:
        conns = set_connection_tags(new_conn["id"], tags, conns)
    return _rebuild_saved_dd(conns), "\u2705 Connection saved", ""


async def handle_test_connection(url: str) -> str:
    """Test a database connection without saving."""
    if not url.strip():
        return "\u26a0\ufe0f Enter a URL to test"
    target = url.strip()
    # Create a temporary pg client for testing
    from .pg_client import PostgresClient
    test_pg = PostgresClient()
    err = await test_pg.connect(target)
    if err:
        return f"\u274c Connection failed: {err}"
    r = await test_pg.execute_sql("SELECT version()")
    await test_pg.disconnect()
    if r.error:
        return f"\u274c Query failed: {r.error}"
    version = r.rows[0][0] if r.rows else "Unknown"
    return f"\u2705 Connection successful!\n{version}"


def handle_export_connections() -> tuple:
    """Export connections to a JSON file for download."""
    import tempfile
    import json
    from datetime import datetime

    conns = load_connections()
    if not conns:
        return None, "\u26a0\ufe0f No connections to export"

    # Prepare export data (mask passwords in URLs)
    export_data = []
    for c in conns:
        url = c.get("value", "")
        # Mask password in URL for security
        masked_url = _mask_password(url) if url else ""
        export_data.append({
            "name": c.get("key", ""),
            "url": masked_url,
            "pinned": c.get("pinned", False),
            "default": c.get("default", False),
            "tags": c.get("tags", []),
        })

    # Create temp file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"pg_connections_{timestamp}.json"
    tmp_path = os.path.join(tempfile.gettempdir(), filename)

    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(export_data, f, indent=2, ensure_ascii=False)

    return tmp_path, f"\u2705 Exported {len(export_data)} connections"


def handle_import_connections(file) -> tuple[dict, str]:
    """Import connections from a JSON file."""
    import json

    if file is None:
        return gr.update(), "\u26a0\ufe0f No file selected"

    try:
        with open(file.name, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return gr.update(), f"\u274c Failed to read file: {e}"

    if not isinstance(data, list):
        return gr.update(), "\u274c Invalid file format — expected JSON array"

    conns = load_connections()
    existing_urls = {c.get("value", "") for c in conns}
    imported = 0
    skipped = 0

    for item in data:
        url = item.get("url", "")
        name = item.get("name", "")

        if not url:
            skipped += 1
            continue

        # Skip if URL already exists
        if url in existing_urls:
            skipped += 1
            continue

        conns = add_connection(url, name, conns)
        existing_urls.add(url)
        imported += 1

    msg = f"\u2705 Imported {imported} connections"
    if skipped:
        msg += f", skipped {skipped} (duplicates or empty)"
    return _rebuild_saved_dd(conns), msg


def handle_delete(display: str, show_val: bool) -> tuple[dict, str, str, str]:
    conn = _find_from_dd(display, show_val)
    if not conn:
        return gr.update(), "", "", ""
    conns = delete_connection(conn["id"])
    conns = load_connections()
    default = get_default(conns)
    if default:
        label = _build_label(default, show_val)
        return _rebuild_saved_dd(conns, show_value=show_val, value=label), default["value"], default["key"], f"Deleted, default: {default['key']}"
    if conns:
        first = conns[0]
        label = _build_label(first, show_val)
        return _rebuild_saved_dd(conns, show_value=show_val, value=label), first["value"], first["key"], f"Deleted, selected: {first['key']}"
    return _rebuild_saved_dd(conns, show_value=show_val), "", "", "Deleted. No connections left."


def handle_show_value_toggle(show: bool, current_dd_val: str) -> dict:
    conns = load_connections()
    choices = build_choices(conns, show_value=show)
    if current_dd_val:
        key, val = parse_display(current_dd_val, show_value=True)
        if not key:
            key = current_dd_val
        conn = find_by_key(key) or (find_by_value(val) if val else None)
        if conn:
            new_val = key if not show else f"{key} # {_mask_password(conn['value'])}"
            if conn.get("pinned"):
                new_val = "\U0001f4cc " + new_val
            return gr.update(choices=choices, value=new_val)
    return gr.update(choices=choices)


def handle_set_default(display: str, show_val: bool) -> tuple[dict, str]:
    conn = _find_from_dd(display, show_val)
    if not conn:
        return gr.update(), "Select a connection first"
    conns = set_default(conn["id"])
    save_env_file({"DEFAULT_CONNECTION_KEY": conn["key"]})
    return _rebuild_saved_dd(conns, show_value=show_val, value=_build_label(conn, show_val)), f"\u2b50 Default set: {conn['key']}"


# ----------------------------------------------------------------------------
# SQL Editor handlers (v1.0.0) — конструктор запросов + палитра объектов
# ----------------------------------------------------------------------------


async def _fetch_schemas() -> list[str]:
    if not pg.is_connected:
        return []
    r = await pg.execute_sql(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name NOT LIKE 'pg_%' AND schema_name != 'information_schema' ORDER BY schema_name"
    )
    if r.error or not r.rows:
        return []
    return [row[0] for row in r.rows]


async def _fetch_tables(schema: str) -> list[str]:
    if not pg.is_connected or not schema:
        return []
    r = await pg.execute_sql(
        f"SELECT table_name FROM information_schema.tables "
        f"WHERE table_schema = '{schema}' AND table_type = 'BASE TABLE' ORDER BY table_name"
    )
    if r.error or not r.rows:
        return []
    return [row[0] for row in r.rows]


async def _fetch_columns(schema: str, table: str) -> list[dict]:
    if not pg.is_connected or not schema or not table:
        return []
    r = await pg.execute_sql(
        f"SELECT column_name, data_type, is_nullable "
        f"FROM information_schema.columns "
        f"WHERE table_schema = '{schema}' AND table_name = '{table}' ORDER BY ordinal_position"
    )
    if r.error or not r.rows:
        return []
    return [{"name": row[0], "type": row[1], "nullable": row[2] == "YES"} for row in r.rows]


async def handle_sql_refresh_schemas() -> tuple:
    """Refresh schemas from connected DB into the object palette."""
    schemas = await _fetch_schemas()
    return gr.update(choices=schemas, value=None), gr.update(choices=[], value=None), gr.update(choices=[], value=None), ""


async def handle_sql_schema_change(schema: str) -> tuple:
    """Schema dropdown changed — обновить таблицы."""
    if not schema:
        return gr.update(choices=[], value=None), gr.update(choices=[], value=None), ""
    tables = await _fetch_tables(schema)
    return gr.update(choices=tables, value=None), gr.update(choices=[], value=None), ""


async def handle_sql_table_change(schema: str, table: str, stmt_type: str) -> tuple:
    """Table changed — обновить колонки и сгенерировать SQL."""
    if not schema or not table:
        return gr.update(choices=[], value=None), ""
    cols = await _fetch_columns(schema, table)
    col_choices = [f"{c['name']} : {c['type']}" for c in cols]
    if stmt_type in ("SELECT", "EXPLAIN SELECT"):
        sql = f"SELECT *\nFROM {schema}.{table}\nLIMIT 100;"
    elif stmt_type == "INSERT":
        col_names = [c["name"] for c in cols]
        cols_part = ", ".join(col_names)
        vals_part = ", ".join(["?"] * len(col_names))
        sql = f"INSERT INTO {schema}.{table} ({cols_part})\nVALUES ({vals_part});"
    elif stmt_type == "UPDATE":
        sql = f"UPDATE {schema}.{table}\nSET column = value\nWHERE condition;"
    elif stmt_type == "DELETE":
        sql = f"DELETE FROM {schema}.{table}\nWHERE condition;"
    elif stmt_type == "TRUNCATE":
        sql = f"TRUNCATE TABLE {schema}.{table};"
    elif stmt_type == "DROP TABLE":
        sql = f"DROP TABLE IF EXISTS {schema}.{table};"
    else:
        sql = f"SELECT *\nFROM {schema}.{table}\nLIMIT 100;"
    return gr.update(choices=col_choices, value=None), sql


def handle_sql_columns_toggle(cols: list[str]) -> str:
    """Пользователь выбрал колонки — вернуть CSV для справки."""
    if not cols:
        return "*"
    return ", ".join(c.split(" :")[0] for c in cols)


async def handle_sql_build(
    stmt_type: str, schema: str, table: str,
    columns: list[str], where: str, order_by: str,
    group_by: str, having: str, limit_val: int,
    join_type: str, join_table: str, join_on: str,
    set_clause: str, insert_values: str, distinct: bool,
) -> str:
    """Построить SQL из параметров формы."""
    try:
        builder = SQLBuilder()
        builder.set_type(stmt_type)
        if schema and table:
            if stmt_type in ("SELECT", "EXPLAIN SELECT"):
                builder.from_table(schema, table)
                if columns:
                    builder.select(*(c.split(" :")[0] for c in columns))
                else:
                    builder.select("*")
                if distinct:
                    builder.distinct()
                if where:
                    builder.where(where)
                if order_by:
                    builder.order_by(order_by)
                if group_by:
                    builder.group_by(group_by)
                if having:
                    builder.having(having)
                if limit_val and limit_val > 0:
                    builder.limit(int(limit_val))
                if join_type and join_type not in ("None", "", None) and join_table and join_on:
                    builder.add_join(join_type, join_table, join_on)
            elif stmt_type == "INSERT":
                builder.insert_into(schema, table)
                if columns:
                    builder.insert_columns(*(c.split(" :")[0] for c in columns))
                if insert_values:
                    builder.insert_values(insert_values)
            elif stmt_type == "UPDATE":
                builder.update_table(schema, table)
                if set_clause:
                    builder.set_values(set_clause)
                if where:
                    builder.where(where)
            elif stmt_type == "DELETE":
                builder.delete_from(schema, table)
                if where:
                    builder.where(where)
            elif stmt_type == "CREATE TABLE":
                builder.create_table(schema, table, "  id SERIAL PRIMARY KEY,\n  name TEXT,\n  created_at TIMESTAMPTZ DEFAULT now()")
            elif stmt_type == "DROP TABLE":
                builder.drop_table(schema, table)
            elif stmt_type == "TRUNCATE":
                builder.truncate(schema, table)
        return builder.build()
    except Exception as e:
        return f"-- Error building SQL: {e}"


async def handle_sql_execute(sql: str) -> str:
    """Выполнить SQL из редактора."""
    if not pg.is_connected:
        return "Not connected to a database."
    if not sql.strip():
        return "Enter a query."
    r = await pg.execute_sql(sql.strip())
    # Сохраняем в историю
    stmt_type = sql.strip().split()[0].upper() if sql.strip() else "SQL"
    get_history().add(sql.strip(), stmt_type, r.duration_ms, r.row_count, r.error)
    if r.error:
        return f"\u274c {r.error}"
    if r.columns:
        header = " | ".join(r.columns)
        sep = "-" * len(header)
        rows_text = "\n".join(
            " | ".join(str(c) if c is not None else "NULL" for c in row)
            for row in r.rows[:SQL_MAX_ROWS_DISPLAY]
        )
        extra = f"\n... +{len(r.rows) - SQL_MAX_ROWS_DISPLAY} rows" if len(r.rows) > SQL_MAX_ROWS_DISPLAY else ""
        return f"\u2705 {r.row_count} rows in {r.duration_ms:.0f}ms\n\n{header}\n{sep}\n{rows_text}{extra}"
    return f"\u2705 OK. {r.row_count} affected in {r.duration_ms:.0f}ms"


async def handle_sql_explain(sql: str) -> str:
    if not pg.is_connected:
        return "Not connected"
    if not sql.strip():
        return "Enter a query."
    return await pg.explain_query(sql)


def handle_sql_format(sql: str) -> str:
    """Форматировать SQL через sqlparse."""
    import sqlparse
    try:
        formatted = sqlparse.format(sql, reindent=True, keyword_case="upper", use_space_around_operators=True)
        return formatted
    except Exception as e:
        return f"-- Format error: {e}\n{sql}"


async def handle_sql_template(name: str, schema: str, table: str) -> str:
    t = get_template_by_name(name)
    if not t:
        return ""
    return apply_template(t, schema, table)


def handle_stmt_type_ui(stmt_type: str) -> tuple:
    """Переключить видимость элементов формы в зависимости от типа оператора."""
    is_select = stmt_type in ("SELECT", "EXPLAIN SELECT")
    is_insert = stmt_type == "INSERT"
    is_update = stmt_type == "UPDATE"
    is_delete = stmt_type == "DELETE"
    return (
        gr.update(visible=is_select),   # columns section
        gr.update(visible=is_select or is_update or is_delete),  # where
        gr.update(visible=is_select),   # order_by
        gr.update(visible=is_select),   # group_by
        gr.update(visible=is_select),   # having
        gr.update(visible=is_select),   # limit
        gr.update(visible=is_select),   # joins
        gr.update(visible=is_select),   # distinct
        gr.update(visible=is_insert),   # insert_values
        gr.update(visible=is_update),   # set_clause
        gr.update(visible=is_delete),   # delete info
    )


async def handle_sql_history_refresh() -> list:
    entries = get_history().get_recent(20)
    return [f"{e['timestamp'][:19]} | {e['type']:8} | {e['duration_ms']:>8.0f}ms | {e['sql'][:80]}" for e in entries]


async def handle_sql_export_csv(sql: str) -> str | None:
    """Экспорт результата запроса в CSV."""
    if not pg.is_connected or not sql.strip():
        return None
    r = await pg.execute_sql(sql.strip())
    if r.error or not r.columns:
        return None
    import csv, io, tempfile
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(r.columns)
    for row in r.rows:
        w.writerow(row)
    path = os.path.join(tempfile.gettempdir(), "pg_export.csv")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(buf.getvalue())
    return path


async def handle_sql_export_json(sql: str) -> str | None:
    if not pg.is_connected or not sql.strip():
        return None
    r = await pg.execute_sql(sql.strip())
    if r.error or not r.columns:
        return None
    import json, tempfile
    data = [dict(zip(r.columns, row)) for row in r.rows]
    path = os.path.join(tempfile.gettempdir(), "pg_export.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
    return path


async def run_sql(sql: str) -> str:
    if not pg.is_connected:
        return "Not connected"
    if not sql.strip():
        return "Enter a query"
    r = await pg.execute_sql(sql.strip())
    if r.error:
        return f"\u274c {r.error}"
    if r.columns:
        header = " | ".join(r.columns)
        sep = "-" * len(header)
        rows = "\n".join(" | ".join(str(c) if c is not None else "NULL" for c in row) for row in r.rows[:SQL_MAX_ROWS_DISPLAY])
        extra = f"\n... +{len(r.rows) - SQL_MAX_ROWS_DISPLAY} rows" if len(r.rows) > SQL_MAX_ROWS_DISPLAY else ""
        return f"\u2705 {r.row_count} rows in {r.duration_ms:.0f}ms\n\n{header}\n{sep}\n{rows}{extra}"
    return f"\u2705 OK. {r.row_count} affected in {r.duration_ms:.0f}ms"


async def get_schema_display() -> str:
    if not pg.is_connected:
        return "Not connected"
    t = await pg.get_schema_text()
    return f"```\n{t}\n```" if t else "No tables"


async def get_health_display() -> str:
    if not pg.is_connected:
        return "Not connected"
    return await pg.get_health_report()


async def get_top_queries_display() -> str:
    if not pg.is_connected:
        return "Not connected"
    return await pg.get_top_queries()


async def run_sql_explain(sql: str) -> str:
    if not pg.is_connected:
        return "Not connected"
    return await pg.explain_query(sql)


# ----------------------------------------------------------------------------
# LLM connection registry handlers (новое в v1.5.0)
# ----------------------------------------------------------------------------


def _active_display() -> str:
    conn = llm_conn_store.get_active_llm_connection(secret_fields_map=SECRET_FIELDS_MAP)
    return _format_active_connection(conn)


def llm_set_active(name: str) -> tuple[dict, str, str]:
    """Делает подключение активным по имени. Обновляет дропдаун реестра
    (активное первым) и оба readonly-показа (Chat + LLM Settings)."""
    if not name:
        return gr.update(), _active_display(), _active_display()
    conns = llm_conn_store.load_llm_connections(secret_fields_map=SECRET_FIELDS_MAP)
    target = next((c for c in conns if c.get("name") == name), None)
    if not target:
        return gr.update(), _active_display(), _active_display()
    llm_conn_store.set_active_llm_connection(target["id"], secret_fields_map=SECRET_FIELDS_MAP)
    save_env_file({"ACTIVE_LLM_CONNECTION": name})
    return gr.update(choices=_llm_conn_choices(), value=name), _active_display(), _active_display()


def llm_delete_connection(name: str) -> tuple[dict, str, str, str]:
    """Удаляет подключение по имени. Если удалили активное — активным
    становится первое оставшееся (логика в store)."""
    if not name:
        return gr.update(), _active_display(), _active_display(), "Select a connection to delete"
    conns = llm_conn_store.load_llm_connections(secret_fields_map=SECRET_FIELDS_MAP)
    target = next((c for c in conns if c.get("name") == name), None)
    if not target:
        return gr.update(), _active_display(), _active_display(), f"Connection '{name}' not found"
    llm_conn_store.delete_llm_connection(target["id"], secret_fields_map=SECRET_FIELDS_MAP)
    new_choices = _llm_conn_choices()
    new_active = _active_display()
    return gr.update(choices=new_choices, value=new_choices[0] if new_choices else None), new_active, new_active, f"\u2705 Deleted '{name}'"


def open_modal_new() -> tuple[dict, str, str, str, str, str, str, str, str, str, str, str, str, str]:
    """Открывает модал для НОВОГО подключения. Сбрасывает селекторы на дефолты."""
    modes = _mode_choices()
    first_mode = modes[0]
    providers = _provider_choices(first_mode)
    first_provider = providers[0][0] if providers else ""
    conn_types = _conn_type_choices(first_provider, first_mode)
    first_ct = conn_types[0][0] if conn_types else ""
    models = _model_choices(first_provider, first_mode, first_ct)
    return _open_modal_with(
        mode=first_mode,
        provider=first_provider,
        conn_type=first_ct,
        model=models[0] if models else "",
        name="",
        conn_id="",
    )


def open_modal_edit(name: str) -> tuple:
    """Открывает модал для РЕДАКТИРОВАНИЯ существующего подключения по имени."""
    if not name:
        return open_modal_new()
    conns = llm_conn_store.load_llm_connections(secret_fields_map=SECRET_FIELDS_MAP)
    conn = next((c for c in conns if c.get("name") == name), None)
    if not conn:
        return open_modal_new()
    return _open_modal_with(
        mode=conn.get("mode", "cloud"),
        provider=conn.get("provider", ""),
        conn_type=conn.get("connection_type", ""),
        model=conn.get("model", ""),
        name=conn.get("name", ""),
        conn_id=conn.get("id", ""),
        params=conn.get("params", {}),
    )


def open_modal_edit_active() -> tuple:
    """Открывает модал на редактирование АКТИВНОГО подключения.
    Не принимает input — сама читает активное из реестра. Если реестр
    пуст — открывает форму для нового (создания). Используется кнопкой
    Edit на вкладке Chat (где нет дропдауна выбора подключения)."""
    active = llm_conn_store.get_active_llm_connection(secret_fields_map=SECRET_FIELDS_MAP)
    if not active:
        return open_modal_new()
    return open_modal_edit(active.get("name", ""))


def _open_modal_with(mode, provider, conn_type, model, name, conn_id="", params=None) -> tuple:
    """Общий помощник: открывает модал и выставляет все поля селекторов +
    видимость динамических параметров под выбранный conn_type."""
    params = params or {}
    # Видимость параметров
    fields = _param_fields_for_ct(conn_type)
    vis = {f: (f in fields) for f in ("api_key", "base_url", "folder_id", "anthropic_version")}

    # Значения параметров (дефолт + переданное)
    def _val(field):
        if params.get(field):
            return params[field]
        return _param_default(conn_type, field, provider, mode)

    return (
        gr.update(visible=True),  # modal
        gr.update(choices=_mode_choices(), value=mode),
        gr.update(choices=[pid for pid, _ in _provider_choices(mode)], value=provider),
        gr.update(choices=[ct for ct, _ in _conn_type_choices(provider, mode)], value=conn_type),
        gr.update(choices=_model_choices(provider, mode, conn_type), value=model),
        gr.update(value=name),
        gr.update(value=_val("api_key"), visible=vis["api_key"]),
        gr.update(value=_val("base_url"), visible=vis["base_url"]),
        gr.update(value=_val("folder_id"), visible=vis["folder_id"]),
        gr.update(value=_val("anthropic_version"), visible=vis["anthropic_version"]),
        gr.update(value=conn_id),  # скрытое поле id
        gr.update(visible=_models_endpoint(conn_type) is not None),  # fetch_btn
        gr.update(),  # fetch_status
        gr.update(),  # llm_status (общий)
    )


def close_modal() -> dict:
    return gr.update(visible=False)


# Reverse mapping: label -> provider_id для конвертации значений из Gradio dropdown
_PROVIDER_LABEL_TO_ID: dict[str, str] = {}


def _build_provider_label_map():
    """Строит маппинг label -> id для всех провайдеров."""
    global _PROVIDER_LABEL_TO_ID
    for section in ("cloud", "local"):
        for pid, pcfg in PROVIDERS.get(section, {}).get("providers", {}).items():
            _PROVIDER_LABEL_TO_ID[pcfg.get("label", pid)] = pid


_build_provider_label_map()


def _resolve_provider(value: str) -> str:
    """Конвертирует label или id провайдера в id."""
    return _PROVIDER_LABEL_TO_ID.get(value, value)


def on_mode_change(mode: str) -> tuple[dict, dict, dict, dict, dict, dict, dict, dict, dict, dict]:
    """При смене Mode: обновить провайдеров, сбросить conn_type/model, видимость полей."""
    providers = _provider_choices(mode)
    first_provider = providers[0][0] if providers else ""
    first_label = providers[0][1] if providers else ""
    logger.info(f"on_mode_change({mode}) -> providers={providers}, first_provider={first_provider}")
    rest = _on_provider_or_ct_change(mode, first_provider, "")
    return (gr.update(choices=[label for _, label in providers], value=first_label),) + rest


def on_provider_change(provider: str, mode: str) -> tuple:
    """При смене Provider: обновить conn_types, сбросить model, видимость полей."""
    pid = _resolve_provider(provider)
    models = _model_choices(pid, mode, _conn_type_choices(pid, mode)[0][0] if _conn_type_choices(pid, mode) else "")
    logger.info(f"on_provider_change({provider} -> {pid}, {mode}) -> models={models}")
    return _on_provider_or_ct_change(mode, pid, "")


def on_conn_type_change(conn_type: str, provider: str, mode: str) -> tuple:
    """При смене Connection Type: обновить model, пересчитать видимость полей."""
    pid = _resolve_provider(provider)
    models = _model_choices(pid, mode, conn_type)
    logger.info(f"on_conn_type_change({conn_type}, {provider} -> {pid}, {mode}) -> models={models}")
    result = _on_provider_or_ct_change(mode, pid, conn_type)
    # Skip first element (conn_type update) — it's the input, not an output
    return result[1:]


def _on_provider_or_ct_change(mode, provider, conn_type) -> tuple:
    """Общий пересчёт зависимых селекторов и видимости параметров.
    Если conn_type пуст — берём первый доступный для провайдера."""
    if not conn_type:
        cts = _conn_type_choices(provider, mode)
        conn_type = cts[0][0] if cts else ""
    models = _model_choices(provider, mode, conn_type)
    fields = _param_fields_for_ct(conn_type)
    vis = {f: (f in fields) for f in ("api_key", "base_url", "folder_id", "anthropic_version")}

    # Дефолты параметров для нового conn_type
    def _val(field):
        return _param_default(conn_type, field, provider, mode)

    return (
        gr.update(choices=[ct for ct, _ in _conn_type_choices(provider, mode)], value=conn_type),
        gr.update(choices=models, value=models[0] if models else ""),
        gr.update(value=_val("api_key"), visible=vis["api_key"]),
        gr.update(value=_val("base_url"), visible=vis["base_url"]),
        gr.update(value=_val("folder_id"), visible=vis["folder_id"]),
        gr.update(value=_val("anthropic_version"), visible=vis["anthropic_version"]),
        gr.update(visible=_models_endpoint(conn_type) is not None),
        gr.update(),  # fetch_status
    )


async def on_fetch_models(provider: str, mode: str, conn_type: str, api_key: str, base_url: str, folder_id: str) -> tuple[dict, str]:
    """Живой запрос списка моделей через LLMClient.fetch_models().
    Обновляет choices model_dd и пишет статус. Не падает при ошибке."""
    if not conn_type or not _models_endpoint(conn_type):
        return gr.update(), "Fetch not supported for this connection type"
    # Собираем временный клиент только для fetch_models
    params = {"api_key": api_key or "", "base_url": base_url or ""}
    if folder_id:
        params["folder_id"] = folder_id
    tmp = LLMClient(
        llm_method=_conn_type_cfg(conn_type).get("llm_method", "openai"),
        model="",
        params=params,
        models_endpoint=_models_endpoint(conn_type),
        models_endpoint_format=_conn_type_cfg(conn_type).get("models_endpoint_format", "openai"),
    )
    models = await tmp.fetch_models()
    if not models:
        return gr.update(), "\u274c No models fetched (check base_url / api_key / server is running)"
    return gr.update(choices=models, value=models[0]), f"\u2705 Fetched {len(models)} models"


def save_connection(
    mode: str,
    provider: str,
    conn_type: str,
    model: str,
    name: str,
    api_key: str,
    base_url: str,
    folder_id: str,
    anthropic_version: str,
    conn_id: str,
) -> tuple[dict, str, str, str, str]:
    """Сохраняет (новое или существующее) подключение в реестр.
    Делает его активным. Закрывает модал. Обновляет дропдаун реестра + оба readonly-показа."""
    name = (name or "").strip()
    if not name:
        logger.warning("save_connection: validation failed — name is empty")
        return gr.update(visible=True), "\u26a0\ufe0f Connection name is required", _active_display(), _active_display(), gr.update()
    if not provider or not conn_type:
        logger.warning("save_connection: validation failed — provider=%s, conn_type=%s", provider, conn_type)
        return gr.update(visible=True), "\u26a0\ufe0f Provider and Connection Type are required", _active_display(), _active_display(), gr.update()
    # Валидация required-параметров
    for field in _param_fields_for_ct(conn_type):
        meta = _param_meta(conn_type, field)
        if meta.get("required"):
            val = {"api_key": api_key, "base_url": base_url, "folder_id": folder_id, "anthropic_version": anthropic_version}.get(field, "")
            if not (val or "").strip():
                label = meta.get("label", field)
                logger.warning("save_connection: validation failed — required field '%s' is empty for conn_type=%s", field, conn_type)
                return gr.update(visible=True), f"\u26a0\ufe0f Field '{label}' is required", _active_display(), _active_display(), gr.update()

    # Сбор params: только поля текущего conn_type (лишние не пишем)
    params = {}
    for field in _param_fields_for_ct(conn_type):
        val = {"api_key": api_key, "base_url": base_url, "folder_id": folder_id, "anthropic_version": anthropic_version}.get(field, "")
        if (val or "").strip():
            params[field] = val.strip()

    conn_record = {
        "mode": mode,
        "provider": provider,
        "connection_type": conn_type,
        "model": model,
        "name": name,
        "params": params,
    }

    logger.info(
        "save_connection: name=%s, mode=%s, provider=%s, conn_type=%s, model=%s, conn_id=%s",
        name, mode, provider, conn_type, model, conn_id or "(new)",
    )

    if conn_id:
        # Редактирование существующего
        llm_conn_store.update_llm_connection(conn_id, conn_record, secret_fields_map=SECRET_FIELDS_MAP)
        llm_conn_store.set_active_llm_connection(conn_id, secret_fields_map=SECRET_FIELDS_MAP)
        save_env_file({"ACTIVE_LLM_CONNECTION": name})
        msg = f"\u2705 Updated and activated '{name}'"
        logger.info("save_connection: updated existing conn_id=%s -> '%s'", conn_id, name)
    else:
        # Проверка уникальности имени
        if llm_conn_store.is_name_taken(name, secret_fields_map=SECRET_FIELDS_MAP):
            logger.warning("save_connection: name '%s' already taken, rejecting", name)
            return (
                gr.update(visible=True),
                f"\u26a0\ufe0f Name '{name}' already used. Choose a different name.",
                _active_display(),
                _active_display(),
                gr.update(),
            )
        llm_conn_store.add_llm_connection(conn_record, make_active=True, secret_fields_map=SECRET_FIELDS_MAP)
        save_env_file({"ACTIVE_LLM_CONNECTION": name})
        msg = f"\u2705 Saved and activated '{name}'"
        logger.info("save_connection: created new connection '%s'", name)

    new_choices = _llm_conn_choices()
    new_active = _active_display()
    logger.info("save_connection: done — '%s' is now active, modal closed", name)
    # Возвращаем: modal(visible=False), llm_status, chat_active_md, llmset_active_md, registry_dd
    return gr.update(visible=False), msg, new_active, new_active, gr.update(choices=new_choices, value=name)


# Auto-save DATABASE_URL on startup if not already saved
conns = load_connections()
db_url = os.getenv("DATABASE_URL", "")
if db_url and not match_existing(db_url, conns) and not match_by_server(db_url, conns):
    conns = add_connection(db_url, _make_key_from_url(db_url), conns)

# Sync .env DEFAULT_CONNECTION_KEY → connections.json default field
default_key_from_env = os.getenv("DEFAULT_CONNECTION_KEY", "").strip()
if default_key_from_env:
    existing_default = get_default(conns)
    if not existing_default or existing_default.get("key") != default_key_from_env:
        target = find_by_key(default_key_from_env)
        if target:
            conns = set_default(target["id"])
            conns = load_connections()

# Determine initial connection from: default > match DATABASE_URL > raw DATABASE_URL
initial_dd = None
initial_key = ""

default_conn = get_default(conns)
if default_conn:
    initial_key = default_conn["key"]
    initial_dd = _build_label(default_conn, SHOW_VAL)
    db_url = default_conn["value"]
elif db_url:
    matched = match_existing(db_url, conns) or match_by_server(db_url, conns)
    if matched:
        initial_key = matched["key"]
        db_url = matched["value"]
        initial_dd = _build_label(matched, SHOW_VAL)
    else:
        initial_key = _make_key_from_url(db_url)
        initial_dd = initial_key

# Логирование статуса шифрования при старте
llm_conn_store.log_encryption_status()

# Auto-connect to DATABASE_URL on startup if set
import asyncio as _asyncio
_initial_status = ""
if db_url:
    try:
        loop = _asyncio.new_event_loop()
        err = loop.run_until_complete(pg.connect(db_url))
        if not err:
            r = loop.run_until_complete(pg.execute_sql("SELECT version()"))
            _initial_status = f"\u2705 Connected\n{r.rows[0][0] if r.rows else ''}" if not r.error else f"\u274c {r.error}"
        else:
            _initial_status = f"\u274c {err}"
        loop.close()
    except Exception as e:
        _initial_status = f"\u274c {e}"

# Начальный показ активного LLM-подключения
_INITIAL_ACTIVE_MD = _active_display()
_INITIAL_LLM_CHOICES = _llm_conn_choices()
_INITIAL_LLM_DD = _INITIAL_LLM_CHOICES[0] if _INITIAL_LLM_CHOICES else None


with gr.Blocks(title=APP_TITLE, css=BLOCKS_CSS, theme=THEME) as app:
    gr.Markdown(f"# {APP_TITLE}")
    gr.Markdown("Standalone PostgreSQL client with AI chat, SQL editor, schema browser, and MCP server.")

    # --- Delete confirmation modal (defined early for reference) ---
    with gr.Column(visible=False, elem_id="llm-modal") as delete_confirm_modal:
        gr.Markdown("### ⚠️ Delete Connection")
        delete_confirm_name = gr.Textbox(label="Connection", interactive=False)
        delete_confirm_info = gr.Textbox(label="", interactive=False, lines=3)
        with gr.Row():
            delete_confirm_yes = gr.Button("\U0001f5d1 Delete", variant="stop", scale=1)
            delete_confirm_no = gr.Button("Cancel", scale=1)
        _delete_dd_ref = gr.Textbox(visible=False)
        _delete_showval_ref = gr.Checkbox(visible=False)

    # --- Change password modal ---
    with gr.Column(visible=False, elem_id="llm-modal") as pw_change_modal:
        gr.Markdown("### \U0001f511 Change Connection Password")
        pw_change_name = gr.Textbox(label="Connection", interactive=False)
        pw_change_host = gr.Textbox(label="Host", interactive=False)
        pw_change_new = gr.Textbox(label="New Password", type="password", placeholder="Enter new password")
        pw_change_confirm = gr.Textbox(label="Confirm Password", type="password", placeholder="Re-enter new password")
        pw_change_status = gr.Textbox(label="", interactive=False)
        with gr.Row():
            pw_change_yes = gr.Button("\U0001f4be Save Password", variant="primary", scale=1)
            pw_change_no = gr.Button("Cancel", scale=1)
        _pw_change_dd_ref = gr.Textbox(visible=False)
        _pw_change_showval_ref = gr.Checkbox(visible=False)

    def open_delete_confirm(display: str, show_val: bool) -> tuple:
        conn = _find_from_dd(display, show_val)
        if not conn:
            return gr.update(visible=False), "", "", display, show_val
        info_lines = []
        if conn.get("key"):
            info_lines.append(f"Name: {conn['key']}")
        if conn.get("value"):
            info_lines.append(f"URL: {conn['value']}")
        return (
            gr.update(visible=True),
            conn.get("key", ""),
            "\n".join(info_lines),
            display,
            show_val,
        )

    def confirm_delete_yes(dd_val: str, show_val: bool) -> tuple:
        return handle_delete(dd_val, show_val) + (gr.update(visible=False),)

    def confirm_delete_no() -> dict:
        return gr.update(visible=False)

    def open_pw_change(display: str, show_val: bool) -> tuple:
        conn = _find_from_dd(display, show_val)
        if not conn:
            return gr.update(visible=False), "", "", "", "", display, show_val
        parsed = _parse_url_parts(conn.get("value", ""))
        return (
            gr.update(visible=True),
            conn.get("key", ""),
            f"{parsed['host']}:{parsed['port']}",
            "",
            "",
            display,
            show_val,
        )

    def confirm_pw_change(dd_val: str, show_val: bool, new_pw: str, confirm_pw: str) -> tuple:
        if not new_pw:
            return gr.update(), "\u274c Password cannot be empty"
        if new_pw != confirm_pw:
            return gr.update(), "\u274c Passwords do not match"
        conn = _find_from_dd(dd_val, show_val)
        if not conn:
            return gr.update(), "\u274c Connection not found"
        parsed = _parse_url_parts(conn.get("value", ""))
        new_url = f"postgresql://{parsed['user']}:{new_pw}@{parsed['host']}:{parsed['port']}/{parsed['database']}"
        conns = update_connection(conn["id"], new_url)
        rebuilt = _rebuild_saved_dd(conns, show_value=show_val)
        return rebuilt, f"\u2705 Password updated for '{conn.get('key', '')}'"

    def confirm_pw_change_and_close(dd_val: str, show_val: bool, new_pw: str, confirm_pw: str):
        saved_dd_update, msg = confirm_pw_change(dd_val, show_val, new_pw, confirm_pw)
        return saved_dd_update, msg, gr.update(visible=False), "", ""

    def cancel_pw_change():
        return gr.update(visible=False), "", "", ""

    with gr.Tabs():
        with gr.TabItem("\U0001f50c Connection"):
            conns = load_connections()
            _saved_cfg = _dd_cfg("connection_tab", "saved_connections")
            _db_cfg = _dd_cfg("connection_tab", "database")

            # === Group 1: Saved Connections + Actions dropdown ===
            with gr.Row():
                saved_dd = gr.Dropdown(
                    label=_saved_cfg.get("label", "Saved Connections"),
                    choices=build_choices(conns, show_value=SHOW_VAL),
                    value=initial_dd,
                    allow_custom_value=not _saved_cfg.get("readonly", False),
                    scale=3,
                    elem_classes="saved-dd",
                )
                # Tag filter
                all_tags = get_all_tags(conns)
                tag_filter_dd = gr.Dropdown(
                    label="\U0001f3f7\ufe0f Filter by Tag",
                    choices=["All"] + all_tags,
                    value="All",
                    interactive=True,
                    scale=1,
                )
                # Grouped actions as dropdown with separators
                conn_action_dd = gr.Dropdown(
                    label="\u2699\ufe0f Actions",
                    choices=[
                        "--- 📌 Management ---",
                        "\U0001f4cc Pin / Unpin",
                        "\u2b50 Set Default",
                        "--- ✏️ Edit ---",
                        "\u270f\ufe0f Rename",
                        "\U0001f50d Edit Connection",
                        "\U0001f511 Change Password",
                        "--- 📦 Import/Export ---",
                        "\U0001f4e4 Export",
                        "\U0001f4e5 Import",
                        "--- ⚠️ Danger ---",
                        "\U0001f5d1 Delete",
                    ],
                    value=None,
                    interactive=True,
                    scale=1,
                )

            # === Group 2: Connection Settings ===
            with gr.Accordion("\U0001f527 Connection Settings", open=True):
                with gr.Row():
                    url_input = gr.Textbox(label="URL", placeholder="postgresql://user:pass@host:5432/db", scale=3, value=db_url)
                    db_selector = gr.Dropdown(
                        label=_db_cfg.get("label", "Database"),
                        choices=[],
                        interactive=True,
                        scale=1,
                        value=None,
                        allow_custom_value=True,
                        elem_classes="saved-dd",
                    )
                with gr.Row():
                    label_input = gr.Textbox(label="Connection name", placeholder="My label or URL", scale=2, value=initial_key)
                    tag_input = gr.Textbox(label="Tags", placeholder="tag1, tag2, ...", scale=1)
                    track_cb = gr.Checkbox(
                        label="Track changes",
                        value=os.getenv("TRACK_CHANGES", "false").lower() == "true",
                        scale=1,
                    )
                    show_value_cb = gr.Checkbox(
                        label="Show URL",
                        value=os.getenv("SHOW_VALUE", "false").lower() == "true",
                        scale=1,
                    )
                save_btn = gr.Button("\U0001f4be Save This URL", variant="primary")
                _editing_conn_id = gr.Textbox(visible=False)  # tracks connection being edited
                import_file = gr.File(label="Import JSON", visible=False, file_types=[".json"])

            # === Group 3: Connection Actions ===
            with gr.Accordion("\U0001f50c Connection Actions", open=True) as actions_section:
                with gr.Row():
                    discover_btn = gr.Button("\U0001f50d Discover Databases", scale=1)
                    test_btn = gr.Button("\u2699\ufe0f Test Connection", scale=1)
                    connect_btn = gr.Button("\U0001f535 Connect", variant="primary", scale=1)
                    disconnect_btn = gr.Button("\u2716 Disconnect", variant="stop", scale=1, visible=False)

            # === Group 4: Status ===
            with gr.Accordion("\U0001f4ca Status / Databases", open=True) as status_section:
                status_display = gr.Textbox(label="", interactive=False, value=_initial_status, elem_id="status_display")

            # === Group 5: Settings (Show/Hide) ===
            _ui_settings = _load_ui_settings()
            with gr.Accordion("\u2699\ufe0f Connection Settings", open=False) as settings_section:
                gr.Markdown("Show or hide sections in the Connection tab. Settings are saved to .env.")
                with gr.Row():
                    settings_show_tag_filter = gr.Checkbox(label="\U0001f3f7\ufe0f Show Tag Filter", value=_ui_settings["show_tag_filter"], scale=1)
                    settings_show_track_changes = gr.Checkbox(label="\U0001f4dd Show Track Changes", value=_ui_settings["show_track_changes"], scale=1)
                    settings_show_conn_actions = gr.Checkbox(label="\U0001f50c Show Connection Actions", value=_ui_settings["show_connection_actions"], scale=1)
                    settings_show_status = gr.Checkbox(label="\U0001f4ca Show Status Section", value=_ui_settings["show_status_section"], scale=1)
                settings_save_btn = gr.Button("\U0001f4be Save Settings", variant="primary")
                settings_status = gr.Textbox(label="", interactive=False)

            # --- Action dispatcher ---
            def _dispatch_conn_action(action: str, display: str, show_val: bool, url: str, label: str, editing_id: str, tags_str: str):
                """Route dropdown action to the appropriate handler."""
                if action is None or action.startswith("---"):
                    return [gr.update()] * 14
                _u = gr.update
                _14 = [_u()] * 14
                if action == "\U0001f4cc Pin / Unpin":
                    r = handle_pin_toggle(display, show_val)
                    return r, _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u()
                if action == "\u2b50 Set Default":
                    r1, r2 = handle_set_default(display, show_val)
                    return r1, _u(), r2, _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u()
                if action == "\u270f\ufe0f Rename":
                    r1, r2 = handle_rename(display, label, show_val)
                    return r1, _u(), r2, _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u()
                if action == "\U0001f50d Edit Connection":
                    u, n, eid, t = handle_edit(display, show_val)
                    return _u(), _u(), _u(), u, n, eid, t, _u(), _u(), _u(), _u(), _u(), _u(), _u()
                if action == "\U0001f511 Change Password":
                    r = open_pw_change(display, show_val)
                    return _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), r[0], _u(), display, gr.update(value=show_val)
                if action == "\U0001f4e4 Export":
                    fp, msg = handle_export_connections()
                    return _u(), _u(), msg, _u(), _u(), _u(), _u(), fp, _u(), _u(), _u(), _u(), _u(), _u()
                if action == "\U0001f4e5 Import":
                    return _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(True)
                if action == "\U0001f5d1 Delete":
                    r = open_delete_confirm(display, show_val)
                    return _u(), _u(), _u(), _u(), _u(), _u(), _u(), _u(), r[0], _u(), _u(), display, _u(), gr.update(value=show_val)
                return _14

            # Outputs: saved_dd, status_display, status_display, url_input, label_input, _editing_conn_id, tag_input, export_file, import_file, delete_confirm_modal, pw_change_modal, _delete_dd_ref, _pw_change_dd_ref, _pw_change_showval_ref
            conn_action_dd.change(
                fn=_dispatch_conn_action,
                inputs=[conn_action_dd, saved_dd, show_value_cb, url_input, label_input, _editing_conn_id, tag_input],
                outputs=[saved_dd, status_display, status_display, url_input, label_input, _editing_conn_id, tag_input, gr.File(visible=False), import_file, delete_confirm_modal, pw_change_modal, _delete_dd_ref, _pw_change_dd_ref, _pw_change_showval_ref],
                queue=False,
            )

            # Reset action dropdown after selection
            conn_action_dd.change(fn=lambda: None, inputs=[], outputs=[conn_action_dd], queue=False)

            # --- Tag filter ---
            tag_filter_dd.change(fn=handle_tag_filter, inputs=[tag_filter_dd, show_value_cb], outputs=[saved_dd], queue=False)

            # --- Direct bindings ---
            saved_dd.select(fn=handle_conn_select, inputs=[saved_dd, url_input], outputs=[url_input, label_input, tag_input], queue=False)
            save_btn.click(fn=handle_save_url_edit, inputs=[url_input, label_input, _editing_conn_id, tag_input], outputs=[saved_dd, status_display, _editing_conn_id], queue=False)
            discover_btn.click(fn=handle_discover, inputs=url_input, outputs=[status_display, db_selector])
            test_btn.click(fn=handle_test_connection, inputs=url_input, outputs=status_display)
            connect_btn.click(
                fn=handle_connect, inputs=[url_input, db_selector], outputs=[status_display, connect_btn, discover_btn, disconnect_btn]
            )
            disconnect_btn.click(fn=handle_disconnect, outputs=[status_display, connect_btn, discover_btn, disconnect_btn])
            db_selector.change(fn=handle_db_select, inputs=[db_selector, url_input], outputs=url_input, queue=False)
            show_value_cb.change(fn=handle_show_value_toggle, inputs=[show_value_cb, saved_dd], outputs=saved_dd, queue=False)
            show_value_cb.change(fn=lambda v: save_env_file({"SHOW_VALUE": str(v).lower()}) or None, inputs=show_value_cb, outputs=[], queue=False)
            track_cb.change(fn=lambda v: save_env_file({"TRACK_CHANGES": str(v).lower()}) or None, inputs=track_cb, outputs=[], queue=False)

            # --- Settings save ---
            settings_save_btn.click(
                fn=handle_save_ui_settings,
                inputs=[settings_show_tag_filter, settings_show_track_changes, settings_show_conn_actions, settings_show_status],
                outputs=[settings_status],
                queue=False,
            )
            # Apply settings on save - toggle visibility
            settings_save_btn.click(
                fn=lambda st, sst, sca, ss: (
                    gr.update(visible=st),   # tag_filter_dd
                    gr.update(visible=sst),  # track_cb
                    gr.update(visible=sca),  # actions_section
                    gr.update(visible=ss),   # status_section
                ),
                inputs=[settings_show_tag_filter, settings_show_track_changes, settings_show_conn_actions, settings_show_status],
                outputs=[tag_filter_dd, track_cb, actions_section, status_section],
                queue=False,
            )

        with gr.TabItem("\U0001f4ac Chat"):
            # readonly-показ активного LLM-подключения + кнопка Edit
            with gr.Row():
                chat_active_md = gr.Markdown(_INITIAL_ACTIVE_MD)
                chat_edit_btn = gr.Button("\u270f Edit LLM Connection", scale=1)
            gr.Markdown("Ask questions about your database in natural language.")
            chatbot = gr.Chatbot(label="Chat", height=450, type="messages")
            msg = gr.Textbox(label="Message", placeholder="e.g. Show me all tables")
            with gr.Row():
                gr.Button("Clear").click(fn=lambda: ([], ""), outputs=[chatbot, msg])
                send_btn = gr.Button("Send", variant="primary")
            # chat_fn больше не принимает mode/provider/model — берёт активное из реестра
            send_btn.click(fn=chat_fn, inputs=[msg, chatbot], outputs=[msg, chatbot])
            msg.submit(fn=chat_fn, inputs=[msg, chatbot], outputs=[msg, chatbot])
            # Edit открывает тот же модал на АКТИВНОЕ подключение — обработчик
            # chat_edit_active() не требует input (берёт активное из реестра сам).
            # Привязка объявлена ниже, после создания модала.

        with gr.TabItem("\U0001f4dd SQL Editor"):
            # --- Top toolbar: stmt type + actions ---
            with gr.Row():
                sql_stmt_type = gr.Dropdown(
                    choices=stmt_type_choices(), value="SELECT", label="Statement type", scale=1, interactive=True,
                )
                _sql_run_btn = gr.Button("Run", variant="primary", scale=1)
                _sql_explain_btn = gr.Button("Explain", scale=1)
                _sql_format_btn = gr.Button("Format", scale=1)
                _sql_clear_btn = gr.Button("Clear", scale=1)
            # --- Main area: object palette (left) + editor+results (right) ---
            with gr.Row():
                # --- Left: Object palette + builder controls ---
                with gr.Column(scale=2):
                    with gr.Row():
                        sql_schema_dd = gr.Dropdown(
                            choices=[], label="Schema", interactive=True, scale=3, allow_custom_value=True,
                        )
                        _sql_refresh_schemas_btn = gr.Button("\U0001f504", size="sm", scale=1, min_width=40)
                    sql_table_dd = gr.Dropdown(choices=[], label="Table", interactive=True, allow_custom_value=True)
                    with gr.Accordion("Columns", open=True) as sql_cols_section:
                        sql_cols_cb = gr.CheckboxGroup(choices=[], label="", interactive=True)
                    with gr.Accordion("Filters & Sorting", open=True):
                        with gr.Row():
                            sql_distinct_cb = gr.Checkbox(label="DISTINCT", visible=True)
                        with gr.Column(visible=True) as sql_where_section:
                            sql_where_tb = gr.Textbox(label="WHERE", placeholder="age > 18", lines=1)
                        with gr.Column(visible=True) as sql_order_section:
                            sql_order_tb = gr.Textbox(label="ORDER BY", placeholder="name ASC", lines=1)
                        with gr.Column(visible=True) as sql_group_section:
                            sql_group_tb = gr.Textbox(label="GROUP BY", placeholder="department", lines=1)
                        with gr.Column(visible=True) as sql_having_section:
                            sql_having_tb = gr.Textbox(label="HAVING", placeholder="count(*) > 5", lines=1)
                        with gr.Column(visible=True) as sql_limit_section:
                            sql_limit_nb = gr.Number(label="LIMIT", value=100, minimum=0, precision=0)
                    with gr.Accordion("Joins", open=False) as sql_joins_section:
                        with gr.Row():
                            sql_join_type = gr.Dropdown(
                                choices=["None", "INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN"],
                                value="None", label="Type", scale=1,
                            )
                            sql_join_table = gr.Textbox(label="Table", placeholder="other_table", scale=1)
                        sql_join_on = gr.Textbox(label="ON", placeholder="users.id = orders.user_id", lines=1)
                    with gr.Column(visible=False) as sql_insert_section:
                        sql_insert_vals = gr.Textbox(label="VALUES", lines=2)
                    with gr.Column(visible=False) as sql_update_section:
                        sql_set_tb = gr.Textbox(label="SET", placeholder="name = 'NewName'", lines=2)
                    with gr.Column(visible=False) as sql_delete_info:
                        gr.Markdown("_DELETE removes rows matching WHERE_")
                    with gr.Accordion("Templates", open=False):
                        sql_template_dd = gr.Dropdown(choices=template_names(), label="", interactive=True)
                        _sql_template_apply_btn = gr.Button("Apply Template", size="sm")
                # --- Right: SQL editor + results ---
                with gr.Column(scale=3):
                    sql_editor = gr.Textbox(
                        label="SQL Editor",
                        placeholder="Select schema/table and configure filters above, or type SQL directly...",
                        lines=8,
                    )
                    _sql_result = gr.Textbox(label="Result", interactive=False, lines=12)
                    with gr.Row():
                        _sql_export_csv = gr.Button("Export CSV", size="sm")
                        _sql_export_json = gr.Button("Export JSON", size="sm")
                        _sql_export_file = gr.File(label="Download", visible=False)
            # --- History ---
            with gr.Row():
                with gr.Column():
                    with gr.Accordion("History", open=False):
                        sql_history_dd = gr.Dropdown(choices=[], label="Recent queries (click to restore)", interactive=True)
                        _sql_history_refresh_btn = gr.Button("Refresh History", size="sm")
            # --- Event wiring ---
            # Refresh schemas from current DB connection
            _sql_refresh_schemas_btn.click(
                fn=handle_sql_refresh_schemas,
                outputs=[sql_schema_dd, sql_table_dd, sql_cols_cb, sql_editor],
            )
            # Schema/table/column browsing
            sql_schema_dd.change(
                fn=handle_sql_schema_change,
                inputs=sql_schema_dd,
                outputs=[sql_table_dd, sql_cols_cb, sql_editor],
            )
            sql_table_dd.change(
                fn=handle_sql_table_change,
                inputs=[sql_schema_dd, sql_table_dd, sql_stmt_type],
                outputs=[sql_cols_cb, sql_editor],
            )
            # Rebuild SQL when any builder control changes
            _sql_builder_inputs = [
                sql_stmt_type, sql_schema_dd, sql_table_dd, sql_cols_cb,
                sql_where_tb, sql_order_tb, sql_group_tb, sql_having_tb,
                sql_limit_nb, sql_join_type, sql_join_table, sql_join_on,
                sql_set_tb, sql_insert_vals, sql_distinct_cb,
            ]
            async def _rebuild_sql(*args):
                """Guard-обёртка: не перестраиваем SQL пока не выбраны schema/table."""
                if not args[1] or not args[2]:  # schema or table empty
                    return ""
                return await handle_sql_build(*args)

            for ctrl in [sql_stmt_type, sql_cols_cb, sql_where_tb, sql_order_tb,
                         sql_group_tb, sql_having_tb, sql_limit_nb, sql_join_type,
                         sql_join_table, sql_join_on, sql_set_tb, sql_insert_vals,
                         sql_distinct_cb]:
                ctrl.change(fn=_rebuild_sql, inputs=_sql_builder_inputs, outputs=sql_editor)
            # Stmt type change → toggle UI sections
            sql_stmt_type.change(fn=handle_stmt_type_ui, inputs=sql_stmt_type, outputs=[
                sql_cols_section, sql_where_section, sql_order_section,
                sql_group_section, sql_having_section, sql_limit_section,
                sql_joins_section, sql_distinct_cb, sql_insert_section,
                sql_update_section, sql_delete_info,
            ])
            # Column selection updates SQL (auto-built via _rebuild_sql)
            # Action buttons
            _sql_run_btn.click(fn=handle_sql_execute, inputs=sql_editor, outputs=_sql_result)
            _sql_explain_btn.click(fn=handle_sql_explain, inputs=sql_editor, outputs=_sql_result)
            _sql_format_btn.click(fn=handle_sql_format, inputs=sql_editor, outputs=sql_editor)
            _sql_clear_btn.click(fn=lambda: ("", ""), outputs=[sql_editor, _sql_result])
            # Templates
            _sql_template_apply_btn.click(
                fn=handle_sql_template,
                inputs=[sql_template_dd, sql_schema_dd, sql_table_dd],
                outputs=sql_editor,
            )
            # History
            _sql_history_refresh_btn.click(fn=handle_sql_history_refresh, outputs=sql_history_dd)
            sql_history_dd.change(
                fn=lambda h: h.split(" | ")[-1].strip() if " | " in (h or "") else "",
                inputs=sql_history_dd,
                outputs=sql_editor,
            )
            # Export
            _sql_export_csv.click(fn=handle_sql_export_csv, inputs=sql_editor, outputs=_sql_export_file)
            _sql_export_json.click(fn=handle_sql_export_json, inputs=sql_editor, outputs=_sql_export_file)

        with gr.TabItem("\U0001f4ca Schema"):
            schema_output = gr.Textbox(label="Full Schema", interactive=False, lines=30)
            gr.Button("\U0001f504 Refresh", variant="primary").click(fn=get_schema_display, outputs=schema_output)

        with gr.TabItem("\U0001f3e5 Health"):
            with gr.Row():
                health_output = gr.Textbox(label="Health Report", interactive=False, lines=15)
                topq_output = gr.Textbox(label="Top Queries", interactive=False, lines=15)
            with gr.Row():
                gr.Button("\U0001f3e5 Health Check", variant="primary").click(fn=get_health_display, outputs=health_output)
                gr.Button("\U0001f422 Top Queries").click(fn=get_top_queries_display, outputs=topq_output)

        with gr.TabItem("\u2699\ufe0f LLM Settings"):
            gr.Markdown("### \U0001f5c4 LLM Connections Registry")
            llmset_active_md = gr.Markdown(_INITIAL_ACTIVE_MD)
            with gr.Row():
                llm_registry_dd = gr.Dropdown(
                    label="Connections",
                    choices=_INITIAL_LLM_CHOICES,
                    value=_INITIAL_LLM_DD,
                    interactive=True,
                    scale=3,
                    elem_classes="saved-dd",
                )
                llm_new_btn = gr.Button("\U0001f195 New", scale=1)
                llm_edit_btn = gr.Button("\u270f Edit", scale=1)
                llm_setactive_btn = gr.Button("\u2714 Set Active", variant="primary", scale=1)
                llm_delete_btn = gr.Button("\U0001f5d1 Delete", variant="stop", scale=1)
            llm_status = gr.Textbox(label="Status", interactive=False)

            gr.Markdown("### \u2699\ufe0f Generation Parameters")
            with gr.Row():
                set_temp = gr.Slider(
                    minimum=LLM_TEMP_MIN,
                    maximum=LLM_TEMP_MAX,
                    step=LLM_TEMP_STEP,
                    value=float(os.getenv("LLM_TEMPERATURE", "0.3")),
                    label="Temperature",
                )
                set_maxtokens = gr.Number(
                    value=int(os.getenv("LLM_MAX_TOKENS", "2000")), label="Max Tokens", minimum=LLM_MAXTOKENS_MIN, maximum=LLM_MAXTOKENS_MAX, step=1
                )
            gr.Markdown("### \U0001f4dd System Prompt")
            set_sysprompt = gr.Textbox(value=get_system_prompt(), label="", lines=6)
            with gr.Row():
                llm_save_btn = gr.Button("\U0001f4be Save Parameters to .env", variant="primary")
                llm_param_status = gr.Textbox(label="", interactive=False, scale=2)
            llm_save_btn.click(
                fn=lambda t, mt, sp: (
                    save_env_file({"LLM_TEMPERATURE": str(t), "LLM_MAX_TOKENS": str(mt), "LLM_SYSTEM_PROMPT": sp}),
                    "\u2705 Saved to .env",
                ),
                inputs=[set_temp, set_maxtokens, set_sysprompt],
                outputs=[llm_param_status],
            )

    # ------------------------------------------------------------------------
    # Modal: объявлен ВНЕ gr.Tabs() — обход бага показа при повторном входе
    # на вкладку. Один модал для Chat (Edit) и LLM Settings (New/Edit).
    # Gradio 6.x не имеет gr.Modal — используем gr.Column с border.
    # ------------------------------------------------------------------------
    with gr.Column(visible=False, elem_id="llm-modal") as llm_modal:
        with gr.Row():
            modal_mode = gr.Dropdown(label="Mode", choices=_mode_choices(), value=_mode_choices()[0], interactive=True, scale=1)
            modal_provider = gr.Dropdown(label="Provider", interactive=True, scale=2)
            modal_conn_type = gr.Dropdown(label="Connection Type", interactive=True, scale=2)
        modal_model = gr.Dropdown(label="Model", interactive=True, allow_custom_value=True)
        with gr.Row():
            modal_fetch_btn = gr.Button("\U0001f50d Fetch models", size="sm")
            modal_fetch_status = gr.Textbox(label="", interactive=False, scale=2)
        gr.Markdown("#### Parameters")
        modal_name = gr.Textbox(label="Connection name", placeholder="My OpenAI / Local Ollama / ...")
        # Динамические поля параметров — предсозданы, visible переключается
        modal_apikey = gr.Textbox(label="API Key", type="password", placeholder="sk-...")
        modal_baseurl = gr.Textbox(label="Base URL", placeholder="https://api.openai.com/v1")
        modal_folder_id = gr.Textbox(label="Folder ID", placeholder="b1g...", visible=False)
        modal_anthropic_version = gr.Textbox(label="anthropic-version", placeholder="2023-06-01", visible=False)
        modal_conn_id = gr.Textbox(visible=False)  # скрытое поле: id редактируемой записи (пусто = новая)
        with gr.Row():
            modal_save_btn = gr.Button("\U0001f4be Save", variant="primary")
            modal_cancel_btn = gr.Button("Cancel")

    # --- Привязки обработчиков модала ---
    modal_mode.change(
        fn=on_mode_change,
        inputs=modal_mode,
        outputs=[
            modal_provider,
            modal_conn_type,
            modal_model,
            modal_apikey,
            modal_baseurl,
            modal_folder_id,
            modal_anthropic_version,
            modal_fetch_btn,
            modal_fetch_status,
        ],
    )
    modal_provider.change(
        fn=on_provider_change,
        inputs=[modal_provider, modal_mode],
        outputs=[
            modal_conn_type,
            modal_model,
            modal_apikey,
            modal_baseurl,
            modal_folder_id,
            modal_anthropic_version,
            modal_fetch_btn,
            modal_fetch_status,
        ],
    )
    modal_conn_type.change(
        fn=on_conn_type_change,
        inputs=[modal_conn_type, modal_provider, modal_mode],
        outputs=[modal_model, modal_apikey, modal_baseurl, modal_folder_id, modal_anthropic_version, modal_fetch_btn, modal_fetch_status],
    )
    modal_fetch_btn.click(
        fn=on_fetch_models,
        inputs=[modal_provider, modal_mode, modal_conn_type, modal_apikey, modal_baseurl, modal_folder_id],
        outputs=[modal_model, modal_fetch_status],
    )
    modal_save_btn.click(
        fn=save_connection,
        inputs=[
            modal_mode,
            modal_provider,
            modal_conn_type,
            modal_model,
            modal_name,
            modal_apikey,
            modal_baseurl,
            modal_folder_id,
            modal_anthropic_version,
            modal_conn_id,
        ],
        outputs=[llm_modal, llm_status, chat_active_md, llmset_active_md, llm_registry_dd],
    )
    modal_cancel_btn.click(fn=close_modal, outputs=llm_modal)

    # --- Кнопки реестра на вкладке LLM Settings ---
    llm_new_btn.click(
        fn=open_modal_new,
        outputs=[
            llm_modal,
            modal_mode,
            modal_provider,
            modal_conn_type,
            modal_model,
            modal_name,
            modal_apikey,
            modal_baseurl,
            modal_folder_id,
            modal_anthropic_version,
            modal_conn_id,
            modal_fetch_btn,
            modal_fetch_status,
            llm_status,
        ],
    )
    llm_edit_btn.click(
        fn=open_modal_edit,
        inputs=llm_registry_dd,
        outputs=[
            llm_modal,
            modal_mode,
            modal_provider,
            modal_conn_type,
            modal_model,
            modal_name,
            modal_apikey,
            modal_baseurl,
            modal_folder_id,
            modal_anthropic_version,
            modal_conn_id,
            modal_fetch_btn,
            modal_fetch_status,
            llm_status,
        ],
    )
    # Chat Edit: открывает модал на редактирование АКТИВНОГО подключения.
    # open_modal_edit_active() не требует input — сама читает активное из реестра.
    chat_edit_btn.click(
        fn=open_modal_edit_active,
        outputs=[
            llm_modal,
            modal_mode,
            modal_provider,
            modal_conn_type,
            modal_model,
            modal_name,
            modal_apikey,
            modal_baseurl,
            modal_folder_id,
            modal_anthropic_version,
            modal_conn_id,
            modal_fetch_btn,
            modal_fetch_status,
            llm_status,
        ],
    )
    llm_setactive_btn.click(
        fn=llm_set_active,
        inputs=llm_registry_dd,
        outputs=[llm_registry_dd, chat_active_md, llmset_active_md],
    )
    llm_delete_btn.click(
        fn=llm_delete_connection,
        inputs=llm_registry_dd,
        outputs=[llm_registry_dd, chat_active_md, llmset_active_md, llm_status],
    )

    # Delete confirmation is handled by the action dispatcher in Connection tab
    delete_confirm_yes.click(
        fn=confirm_delete_yes,
        inputs=[_delete_dd_ref, _delete_showval_ref],
        outputs=[saved_dd, url_input, label_input, status_display, delete_confirm_modal],
        queue=False,
    )
    delete_confirm_no.click(fn=confirm_delete_no, outputs=[delete_confirm_modal], queue=False)

    # Password change confirmation
    pw_change_yes.click(
        fn=confirm_pw_change_and_close,
        inputs=[_pw_change_dd_ref, _pw_change_showval_ref, pw_change_new, pw_change_confirm],
        outputs=[saved_dd, pw_change_status, pw_change_modal, pw_change_new, pw_change_confirm],
        queue=False,
    )
    pw_change_no.click(fn=cancel_pw_change, outputs=[pw_change_modal, pw_change_new, pw_change_confirm, pw_change_status], queue=False)

    gr.Markdown("---\n*PostgreSQL MCP Autonomous \u2014 Standalone. No subscriptions. Source code available.*")


if __name__ == "__main__":
    try:
        app.launch(server_name=APP_HOST, server_port=APP_PORT, share=False, show_error=True)
    except Exception as e:
        logging.getLogger("pg_mcp").warning("Launch issue: %s", e)
