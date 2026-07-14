# =========================================================================
# VERSION: 1.2.0
# Path: src/postgres_mcp/autonomous/llm_client.py
# Изменения в 1.2.0:
#  - НОВАЯ модель конструирования: LLMClient(llm_method, model, params)
#    вместо прежних (provider, model, api_key, base_url). llm_method явно
#    задаёт формат API (openai | anthropic | google) и не привязан к имени
#    провайдера — теперь один и тот же openai-формат используется и для
#    OpenAI, и для VseGPT, и для Ollama, и для Yandex-шлюза.
#  - Все параметры подключения читаются из единого словаря params
#    (api_key, base_url, folder_id, anthropic_version). Так один клиент
#    обслуживает произвольный набор полей, определённый в providers.yaml.
#  - НОВЫЙ метод fetch_models(): GET {base_url}{models_endpoint} для
#    живого получения списка моделей (кнопка "Fetch models" в UI).
#  - НОВАЯ функция build_llm_client_from_connection(conn): собирает клиент
#    из записи реестра llm_connections.json.
#  - Обратная совместимость: get_llm_client() (старый .env-путь) сохранён
#    как fallback для случая, когда реестр llm_connections.json пуст/отсутствует.
# Изменения в 1.1.0:
#  - Base URL провайдеров / LLM_HTTP_TIMEOUT / ANTHROPIC_API_VERSION вынесены в .env.
# =========================================================================

import json
import logging
import os
from dataclasses import dataclass
from dataclasses import field
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class LLMResponse:
    content: Optional[str]
    tool_calls: list = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str = "stop"


class LLMClient:
    """Универсальный LLM-клиент для 3 форматов API: openai, anthropic, google.

    Аргументы:
        llm_method: "openai" | "anthropic" | "google" — какой формат API использовать.
        model: имя модели (например "gpt-4o-mini").
        params: словарь параметров подключения. Минимальный набор:
            openai    -> {api_key?, base_url}
            anthropic -> {api_key, base_url?, anthropic_version?}
            google    -> {api_key, base_url?}
            yandex    -> {api_key, folder_id, base_url} (через openai-формат,
                         folder_id добавляется в заголовок x-folder-id).
        models_endpoint: относительный путь для fetch_models (например "/models"),
            None если live-запрос не поддерживается.
        models_endpoint_format: "openai" — формат ответа.
    """

    def __init__(
        self,
        llm_method: str,
        model: str,
        params: Optional[dict] = None,
        models_endpoint: Optional[str] = None,
        models_endpoint_format: str = "openai",
    ):
        self.llm_method = (llm_method or "openai").lower()
        self.model = model
        self.params = dict(params or {})
        self.models_endpoint = models_endpoint
        self.models_endpoint_format = models_endpoint_format
        # Таймаут читается здесь (не на уровне модуля), чтобы гарантированно
        # подхватить .env независимо от порядка импортов.
        self.timeout = float(os.getenv("LLM_HTTP_TIMEOUT", "60"))

    # Удобные accessors для часто используемых полей
    @property
    def api_key(self) -> str:
        return self.params.get("api_key", "") or ""

    @property
    def base_url(self) -> str:
        return (self.params.get("base_url", "") or "").rstrip("/")

    @property
    def anthropic_version(self) -> str:
        return self.params.get("anthropic_version", "") or os.getenv("ANTHROPIC_API_VERSION", "2023-06-01")

    async def chat(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
        tools: Optional[list[dict]] = None,
        temperature: float = 0.3,
        max_tokens: int = 2000,
    ):
        if self.llm_method == "anthropic":
            return await self._chat_anthropic(messages, system_prompt, tools, temperature, max_tokens)
        if self.llm_method == "google":
            return await self._chat_google(messages, system_prompt, tools, temperature, max_tokens)
        return await self._chat_openai(messages, system_prompt, tools, temperature, max_tokens)

    async def _chat_openai(self, messages, system_prompt, tools, temperature, max_tokens):
        full = [{"role": "system", "content": system_prompt}] if system_prompt else []
        full.extend(messages)
        body = {"model": self.model, "messages": full, "temperature": temperature, "max_tokens": max_tokens}
        if tools:
            body["tools"] = [{"type": "function", "function": t} for t in tools]
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # Yandex OpenAI-совместимый шлюз требует folder_id в заголовке
        folder_id = self.params.get("folder_id")
        if folder_id:
            headers["x-folder-id"] = folder_id
        url = f"{self.base_url}/chat/completions"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        choice = data["choices"][0]
        msg = choice["message"]
        usage = data.get("usage", {})
        tool_calls = [
            {"id": tc["id"], "name": tc["function"]["name"], "arguments": json.loads(tc["function"]["arguments"])} for tc in msg.get("tool_calls", [])
        ]
        return LLMResponse(
            content=msg.get("content"),
            tool_calls=tool_calls,
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            finish_reason=choice.get("finish_reason", "stop"),
        )

    async def _chat_anthropic(self, messages, system_prompt, tools, temperature, max_tokens):
        body = {"model": self.model, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
        if system_prompt:
            body["system"] = system_prompt
        if tools:
            body["tools"] = [{"name": t["name"], "description": t.get("description", ""), "input_schema": t.get("parameters", {})} for t in tools]
        headers = {"x-api-key": self.api_key, "anthropic-version": self.anthropic_version, "content-type": "application/json"}
        url = f"{self.base_url}/messages"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        blocks = data.get("content", [])
        text = "\n".join(b["text"] for b in blocks if b.get("type") == "text")
        tool_calls = [{"id": b["id"], "name": b["name"], "arguments": b.get("input", {})} for b in blocks if b.get("type") == "tool_use"]
        usage = data.get("usage", {})
        return LLMResponse(
            content=text or None, tool_calls=tool_calls, input_tokens=usage.get("input_tokens", 0), output_tokens=usage.get("output_tokens", 0)
        )

    async def _chat_google(self, messages, system_prompt, tools, temperature, max_tokens):
        contents = [{"role": "user" if m["role"] == "user" else "model", "parts": [{"text": m["content"]}]} for m in messages]
        body = {"contents": contents, "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}}
        if system_prompt:
            body["systemInstruction"] = {"parts": [{"text": system_prompt}]}
        url = f"{self.base_url}/models/{self.model}:generateContent?key={self.api_key}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=body)
            resp.raise_for_status()
            data = resp.json()
        candidate = data.get("candidates", [{}])[0]
        text = "".join(p.get("text", "") for p in candidate.get("content", {}).get("parts", []))
        return LLMResponse(content=text or None)

    async def fetch_models(self) -> list[str]:
        """Живой запрос списка моделей. Работает только если models_endpoint
        задан (провайдер поддерживает /models или аналог). Возвращает список
        имён моделей; при ошибке — пустой список (не падает)."""
        if not self.models_endpoint or not self.base_url:
            return []
        # base_url уже без хвостового '/', models_endpoint начинается с '/'
        url = f"{self.base_url}{self.models_endpoint}"
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        folder_id = self.params.get("folder_id")
        if folder_id:
            headers["x-folder-id"] = folder_id
        fetch_timeout = float(os.getenv("LLM_FETCH_MODELS_TIMEOUT", "10"))
        try:
            async with httpx.AsyncClient(timeout=fetch_timeout) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning("fetch_models failed for %s: %s", url, e)
            return []
        # Стандартный OpenAI-формат: {"data": [{"id": "model-name"}, ...]}
        if self.models_endpoint_format == "openai":
            items = data.get("data", []) if isinstance(data, dict) else []
            return [it.get("id") for it in items if isinstance(it, dict) and it.get("id")]
        return []


def build_llm_client_from_connection(conn: dict, providers_catalog: dict) -> LLMClient:
    """Собирает LLMClient из записи реестра llm_connections.json.
    conn: {mode, provider, connection_type, model, params}
    providers_catalog: содержимое config/providers.yaml (для определения
        llm_method и models_endpoint по connection_type).
    """
    ct_name = conn.get("connection_type", "")
    ct_cfg = (providers_catalog or {}).get("connection_types", {}).get(ct_name, {})
    llm_method = ct_cfg.get("llm_method", "openai")
    models_endpoint = ct_cfg.get("models_endpoint")
    models_endpoint_format = ct_cfg.get("models_endpoint_format", "openai")
    return LLMClient(
        llm_method=llm_method,
        model=conn.get("model", ""),
        params=conn.get("params", {}),
        models_endpoint=models_endpoint,
        models_endpoint_format=models_endpoint_format,
    )


def get_llm_client() -> LLMClient:
    """FALLBACK-конструктор из плоских переменных .env (старый путь).
    Используется, когда реестр llm_connections.json пуст или отсутствует —
    обеспечивает обратную совместимость и работоспособность "из коробки"
    после миграции. Не используется при активном подключении из реестра."""
    provider = os.getenv("LLM_PROVIDER", "openai")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")
    params = {}
    if provider == "openai":
        params["api_key"] = os.getenv("OPENAI_API_KEY", "")
        params["base_url"] = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        return LLMClient("openai", model, params, models_endpoint="/models")
    if provider == "anthropic":
        params["api_key"] = os.getenv("ANTHROPIC_API_KEY", "")
        params["base_url"] = os.getenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
        params["anthropic_version"] = os.getenv("ANTHROPIC_API_VERSION", "2023-06-01")
        return LLMClient("anthropic", model, params)
    if provider == "google":
        params["api_key"] = os.getenv("GOOGLE_API_KEY", "")
        params["base_url"] = os.getenv("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta")
        return LLMClient("google", model, params)
    if provider == "local":
        params["base_url"] = os.getenv("LOCAL_LLM_BASE_URL", "http://localhost:1234/v1")
        return LLMClient("openai", model, params, models_endpoint="/models")
    # Неизвестный провайдер — по умолчанию openai-формат
    params["base_url"] = os.getenv("LOCAL_LLM_BASE_URL", "")
    return LLMClient("openai", model, params)
