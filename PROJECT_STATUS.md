# PROJECT_STATUS — PostgreSQL MCP Autonomous: конфигурация UI/поведения

## Цель задачи
1. ReadOnly для дропдаунов, которые не должны редактироваться (не все — уточняется какие)
2. Вынести настройки списков вкладки Connection в отдельный файл `*.yaml`
3. Все переключатели/режимы/условия → в `.env` с подробным описанием
4. Найти прочие захардкоженные значения (модели, параметры моделей, промпты) → вынести в конфиг
5. Версионировать каждый изменённый файл: `# VERSION: x.x.x` + `# Path: ...` + описание правки
6. Недостающие модули — список и стоп
7. В конце — упаковать в zip

## Статус: ЭТАП 1 — ЗАВЕРШЁН (упаковано в архив)

Реализовано и упаковано:
- config/ui_settings.yaml (v1.0.0) — новый
- config/prompts.yaml (v1.0.0) — новый
- .env (v1.1.0) — обновлён, добавлены секции
- src/postgres_mcp/autonomous/app.py (v1.1.0) — обновлён
- src/postgres_mcp/autonomous/pg_client.py (v1.1.0) — обновлён
- CHANGES_README.md — инструкция по установке с точными путями

Синтаксис Python и YAML проверены (py_compile + yaml.safe_load) — OK.

## Статус: ЭТАП 2 — Аудит llm_client.py / mcp_server.py (ТЕКУЩИЙ)

### Аудит llm_client.py
- Base URL по провайдерам захардкожены в `__init__`:
  openai: https://api.openai.com/v1
  anthropic: https://api.anthropic.com/v1
  google: https://generativelanguage.googleapis.com/v1beta
  → предлагаю вынести в .env (OPENAI_BASE_URL / ANTHROPIC_BASE_URL / GOOGLE_BASE_URL),
    fallback = текущие значения. Нужно для корпоративных прокси/гейтвеев,
    совместимых по API. Низкий риск — поведение не меняется, если не задано.
- `httpx.AsyncClient(timeout=60)` — захардкожено в 3 местах (openai/anthropic/google)
  → вынести в LLM_HTTP_TIMEOUT
- `anthropic-version: "2023-06-01"` — версия Anthropic API в заголовке
  → вынести в ANTHROPIC_API_VERSION (Anthropic периодически обновляет эту дату)
- **НАЙДЕНА ФУНКЦИОНАЛЬНАЯ ПРОБЛЕМА (не просто хардкод):**
  `chat_fn()` в app.py вызывает `llm.chat(msgs, system_prompt, tools)` —
  БЕЗ передачи temperature/max_tokens. Из-за этого `LLMClient.chat()` всегда
  использует свои дефолты прямо в сигнатуре (temperature=0.3, max_tokens=2000),
  и слайдер Temperature / поле Max Tokens на вкладке LLM Settings **никак
  не влияют на реальные ответы в чате** — только сохраняются в .env, но не
  читаются на момент вызова. Вопрос пользователю — см. ниже.

### Аудит mcp_server.py
- `explain_query` MCP-инструмент делает СВОЙ inline-запрос
  `EXPLAIN (FORMAT JSON, ANALYZE ...)`, а не переиспользует
  `pg_client.explain_query()` (который мы уже параметризовали в этапе 1
  через EXPLAIN_FORMAT). Дублирование логики + хардкод FORMAT JSON здесь.
  → привести к общим EXPLAIN_FORMAT/EXPLAIN_ANALYZE
- `get_top_queries` tool schema: `"default": 10` — дублирует
  TOP_QUERIES_DEFAULT_LIMIT (этап 1), но здесь отдельный хардкод
  → привести к общему значению
- `load_dotenv()` вызывается БЕЗ явного пути (в отличие от app.py, где путь
  вычисляется абсолютно). Если mcp_server.py запущен не из корня проекта
  (например, как отдельный процесс из claude_desktop_config.json — что
  явно описано в докстринге файла) — .env может не найтись.
  → предлагаю выровнять с app.py (абсолютный путь от расположения файла).
  Чисто надёжность, поведение не меняется, если .env и так находился.
- `serverInfo.name`/`version`, `protocolVersion` — оставляю как есть:
  это структурные идентификаторы MCP-протокола/сервера, не "переключатели
  условий" в смысле вашего запроса. Не трогаю без отдельного запроса.

### Аудит connection_store.py
- Без изменений в этом этапе. Единственное потенциально конфигурируемое
  место — имя подпапки "postgres-mcp" в APPDATA при CONNECTIONS_STORE=global
  (`get_store_path()`), но это по сути имя приложения — низкий приоритет,
  не трогаю без запроса.

## Уточняющий вопрос — РАУНД 3 (ОТВЕЧЕНО)
1. Баг temperature/max_tokens в чате — **исправить**.
2. Base URL/таймаут в llm_client.py — **вынести всё**.
3. mcp_server.py: EXPLAIN + load_dotenv() — **оба исправления**.

## Статус: ЭТАП 2 — ЗАВЕРШЁН (упаковано в архив)

Реализовано:
- app.py → v1.2.0: chat_fn() теперь передаёт temperature/max_tokens
  (читаются из .env на каждый вызов) в LLMClient.chat() — баг исправлен
- llm_client.py → v1.1.0 (новый в сборке): base URL по провайдерам,
  LLM_HTTP_TIMEOUT, ANTHROPIC_API_VERSION — все из .env с fallback
- mcp_server.py → v1.1.0 (новый в сборке): абсолютный путь .env,
  explain_query использует общие EXPLAIN_FORMAT/EXPLAIN_ANALYZE,
  get_top_queries лимит приведён к общему TOP_QUERIES_DEFAULT_LIMIT
- connection_store.py — скопирован БЕЗ изменений (для целостности пакета)
- .env → v1.2.0: добавлены OPENAI_BASE_URL/ANTHROPIC_BASE_URL/
  GOOGLE_BASE_URL, ANTHROPIC_API_VERSION, LLM_HTTP_TIMEOUT

py_compile: все 5 файлов — OK.

## Статус: ЭТАП 3 — Аудит инфраструктурных файлов (ТЕКУЩИЙ, жду уточнений)

### Получено
- `import.py` — утилита-сканер импортов проекта (не часть runtime-приложения)
- `Dockerfile` и `Dockerfile-en` — содержимое ИДЕНТИЧНО в присланном тексте
- `devenv.lock` — JSON, автогенерируемый lock-файл (Nix flake inputs)
- `devenv.nix` — реальный Nix-конфиг окружения (python/js/postgres_16/uv)
- Отдельно в теле сообщения (не как документ): `devenv.yaml` (с
  `nixpkgs`/`nixpkgs-unstable`/`nixpkgs-python` inputs) и укороченный
  `devenv-en.yaml`-подобный фрагмент
- Документ 14 — это НЕ содержимое файла проекта, а текст-объяснение
  (судя по формулировкам "Вот полный рабочий devenv.yaml... Если хотите,
  я могу также предоставить...") — похоже на ответ другого AI-чата с
  ПРЕДЛОЖЕНИЕМ альтернативного devenv.yaml (с `services.postgresql`,
  зависимостью `fastmcp`, другой структурой inputs). Это расходится
  с реальным `devenv.nix`, который был прислан (там Postgres-сервис
  закомментирован, `dotenv.enable`, нет `fastmcp`).

### Открытые вопросы (ОТВЕЧЕНО)
1. Документ 14 (fastmcp/postgres-сервис) — **внедрить идеи в реальные файлы**.
2. devenv.lock — **пропустить**, не трогать.
3. Dockerfile/Dockerfile-en идентичны — **не трогать содержимое**;
   import.py — **поправить путь**.

## Статус: ЭТАП 3 — ЗАВЕРШЁН (упаковано в отдельный архив)

Реализовано:
- `import.py` → v1.1.0: `project_path` переведён на raw-строку (был
  невалидный escape в обычной строке)
- `Dockerfile` → v1.0.1, `Dockerfile-en` → v1.0.1: только версионные
  заголовки, тело идентично (сверено diff'ом)
- `devenv.nix` → v1.1.0: включён `services.postgres` (был закомментирован)
  — даёт `pg_stat_statements`, необходимый для Top Queries в приложении.
  Отмечено расхождение портов (devenv-Postgres на 5444, DATABASE_URL
  по умолчанию на 5432) — не менял самовольно, оставил на решение
  пользователя.
- `devenv.yaml` → v1.1.0, `devenv-en.yaml` → v1.1.0: версionные заголовки.
  ⚠️ ДОПУЩЕНИЕ: файлы не были явно подписаны в исходном сообщении —
  определены по содержимому (какой из двух yaml-блоков какому файлу
  соответствует). Явно помечено в самих файлах, ждёт подтверждения.
- **fastmcp — НЕ добавлен, работа остановлена на этом пункте.**
  Причина: (1) это Python-зависимость через pyproject.toml/uv, а не
  Nix-пакет; (2) pyproject.toml не предоставлялся; (3) текущий
  mcp_server.py реализует MCP-протокол вручную, не через FastMCP —
  добавление пакета само по себе ничего не изменит без переписывания
  mcp_server.py под FastMCP API, что является отдельной архитектурной
  задачей.

## Статус: ЭТАП 4 — ЗАВЕРШЁН (архитектурная переработка, финальный сводный архив)

### Главное: mcp_server.py переписан на официальный FastMCP API
`from mcp.server.fastmcp import FastMCP` — уже часть зависимости
`mcp[cli]>=1.5.0` (подтверждено веб-поиском: FastMCP 1.0 включена в
официальный MCP Python SDK с 2024 года как `mcp.server.fastmcp`).
**Новая зависимость НЕ понадобилась** — блокер с недостающим
`pyproject.toml` из этапа 3 снят естественным образом.

Каждый из 7 инструментов стал обычной async-функцией с типизированными
аргументами и Google-style docstring под `@mcp.tool()` — FastMCP сам
генерирует JSON Schema и берёт на себя обработку ошибок (unhandled
exception → tool-error клиенту), транспорт (stdio/sse через
`mcp.run(transport=...)`). Ручной JSON-RPC/stdin-луп и свой SSE-сервер
на aiohttp — полностью убраны.

### 🔴 Найден и исправлен критический баг: pyproject.toml не парсился
Файл содержал вставленные markdown-фенсы (` ```toml ` / ` ``` `) —
видимо, скопировано из чата вместе с обрамлением кодового блока.
Проверено: `tomllib.load()` падал с `Invalid statement (at line 25)`.
**Проект в таком виде не собрался бы** (`uv sync`/`pip install`/
`hatchling build` все упали бы на парсинге). Исправлено — убраны только
фенсы, структура/зависимости не менялись. Проверено повторно — парсится.

### Остальные правки этапа 4
- `devenv.nix` / `devenv-en.nix` → v1.2.0: порт devenv-Postgres переведён
  с 5444 на 5432 (совпадает с портом по умолчанию в `.env DATABASE_URL`)
  — расхождение портов, отмеченное в этапе 3, устранено
- `devenv-all.yaml` → помечен как справочный (не рабочий конфиг) файл,
  подробно расписано, какие идеи из него уже внедрены (Postgres-сервис,
  FastMCP), а какие — осознанно нет (дублирование Python-зависимостей
  напрямую в devenv, отдельный DATABASE_URL внутри devenv.yaml и т.д.)
- `pyproject-en.toml` → только заголовок (файл и так был валиден)
- Подтверждён маппинг файлов из этапа 3 (Dockerfile/-en идентичны,
  devenv.yaml/-en.yaml соответствуют предположению) — реальные аплоады
  совпали 1:1 с тем, что было сделано вслепую

Финальный сводный архив содержит ВСЕ файлы проекта, тронутые за все
4 этапа, в их последних версиях: `.env`, `config/*.yaml`,
`src/postgres_mcp/autonomous/*.py` (app/pg_client/llm_client/
connection_store/mcp_server), `import.py`, `Dockerfile(-en)`,
`devenv.nix(-en)`, `devenv.yaml(-en)`, `devenv-all.yaml`,
`pyproject(-en).toml`.

py_compile (все .py) + yaml.safe_load (все .yaml) + tomllib.load
(оба .toml) — все проверки пройдены.

## Не хватает / не запрошено явно (для возможного следующего этапа)
- `docker-compose.yml` не запрашивался — не создавался
- Пункты из `devenv-all.yaml`, помеченные "НЕ внедрено" — ждут вашего
  решения (см. сам файл)
- `import.py` остаётся debug/audit-утилитой вне runtime-приложения —
  не интегрирован в CI/тесты (не запрашивалось)

## Аудит захардкоженных значений (app.py, connection_store.py, pg_client.py)

### Дропдауны на вкладке Connection
| Компонент | allow_custom_value | Заполняется | Текущее поведение |
|---|---|---|---|
| `saved_dd` (Saved Connections) | True | из connections.json | можно вписать текст вручную |
| `db_selector` (Database) | True | из Discover Databases | можно вписать текст вручную |

### Дропдауны/радио на вкладке Chat
| Компонент | allow_custom_value | Choices |
|---|---|---|
| `mode_radio` | — (Radio) | ["remote", "local"] — хардкод |
| `provider_dd` | False | ["openai","anthropic","google","local"] — хардкод |
| `model_dd` | True | COMMON_MODELS[provider] — хардкод словарь |

### Вкладка LLM Settings
| Компонент | Choices/Default |
|---|---|
| `set_provider` | ["openai","anthropic","google"] — хардкод |
| `set_temp` (Slider) | min=0, max=2, step=0.05, default=0.3 |
| `set_maxtokens` (Number) | min=1, max=100000, default=2000 |
| `set_sysprompt` | default = SYSTEM_PROMPT (хардкод многострочный) |
| `set_baseurl` | default "http://localhost:1234/v1" |

### Хардкод-словарь моделей (COMMON_MODELS)
openai/anthropic/google/local списки моделей — сейчас в коде, не в конфиге.

### Прочие хардкод-константы, найденные в коде
- `SYSTEM_PROMPT` — системный промпт для чата (app.py)
- `POSTGRES_TOOLS` — определения tool-функций для LLM (структурные, не конфиг — под вопросом)
- `chat_fn`: `for _ in range(5)` — макс. итераций tool-calling цикла
- `chat_fn`: `text[:1000]` — обрезка текста результата инструмента в выводе
- `run_sql`: `r.rows[:100]` — лимит строк в выводе SQL Editor
- `PostgresClient.get_schema`: `info.schemas[:10]` — лимит схем при построении схемы БД
- `PostgresClient.get_top_queries`: `limit: int = 10` — лимит топ-запросов по умолчанию
- `PostgresClient.explain_query`: `"EXPLAIN (FORMAT JSON, ANALYZE false)"` — режим EXPLAIN хардкод
- `PostgresClient.connect`: `min_size=1, max_size=5, timeout=10, command_timeout=30` — параметры пула соединений
- UI-размеры: `gr.Chatbot(height=450)`, `lines=6/15/30` в разных Textbox — вероятно НЕ трогаем (чисто визуальное, не логика)
- `THEME = "soft"` — уже в .env, но нигде не используется в коде (theme="default" хардкожен в `app.launch(...)`) — расхождение, нужно уточнить

## Уточняющие вопросы — РАУНД 1 (ОТВЕЧЕНО)
1. ReadOnly дропдауны: **Database → readonly** (allow_custom_value=False, interactive=True но без свободного ввода), **Saved Connections → остаётся редактируемым** (allow_custom_value=True сохраняется).
2. Формат: **ui_settings.yaml — широкий**, содержит readonly/choices/labels для ВСЕХ дропдаунов проекта (Connection + Chat + LLM Settings).
3. Scope: **Chat/LLM Settings выносим в этом же этапе** — COMMON_MODELS, choices провайдеров, System Prompt, температура/токены по умолчанию — всё туда же.

## Уточняющие вопросы — РАУНД 2 (ЖДУ ОТВЕТА)
Отправлены — см. следующее сообщение ассистента (модуль PyYAML + операционные лимиты SQL/pool).

## Изменённые файлы (версии)
_(пока нет)_

## Модули, которых может не хватать
- PyYAML (`import yaml`) — нужен для чтения `ui_settings.yaml`. Наличие в requirements.txt/pyproject.toml НЕ подтверждено — файлы не предоставлялись. Ждём подтверждения от пользователя.

## Итоговая структура (утверждено на данный момент)
- `.env` — расширяется: режимы, дефолты температуры/токенов, дефолтная модель, base URL, флаги (SHOW_VALUE/TRACK_CHANGES уже есть)
- `config/ui_settings.yaml` — новый файл: choices/labels/readonly для дропдаунов (Saved Connections, Database, Mode, Provider (chat), Model (chat), Provider (LLM Settings)) + COMMON_MODELS (словарь моделей по провайдерам) + System Prompt (либо здесь, либо в .env — см. вопрос 2 раунда 2)
- `app.py` — читает оба файла при старте, версионируется
