# PostgreSQL MCP Autonomous — вынос конфигурации, этап 1

## Куда класть файлы (относительно G:\postgres-mcp)

| Файл в архиве | Куда положить (замена/новый файл) |
|---|---|
| `.env` | `G:\postgres-mcp\.env` — **ЗАМЕНИТЬ** (проверьте DATABASE_URL/ключи API — в архиве они как в вашем примере, пустые/тестовые, перенесите свои реальные значения!) |
| `config/ui_settings.yaml` | `G:\postgres-mcp\config\ui_settings.yaml` — новый файл, папку `config` нужно создать |
| `config/prompts.yaml` | `G:\postgres-mcp\config\prompts.yaml` — новый файл |
| `src/postgres_mcp/autonomous/app.py` | `G:\postgres-mcp\src\postgres_mcp\autonomous\app.py` — **ЗАМЕНИТЬ** |
| `src/postgres_mcp/autonomous/pg_client.py` | `G:\postgres-mcp\src\postgres_mcp\autonomous\pg_client.py` — **ЗАМЕНИТЬ** |

## ⚠️ Важно перед заменой .env
В архиве `.env` содержит только структуру из вашего примера (DATABASE_URL с плейсхолдером `user:password`, пустые API-ключи). **Перед заменой скопируйте реальные значения** из вашего текущего `.env` (пароль от БД, API-ключи, реальный DEFAULT_CONNECTION_KEY и т.д.) — либо просто вручную допишите новые секции (после строки `LOCAL_LLM_MODEL=local-model` и до `# --- UI ---`) в ваш существующий файл, не затирая его целиком.

## Что изменилось (кратко)
- **ReadOnly**: `Database` теперь только выбор из списка (после Discover Databases), `Saved Connections` остался редактируемым
- **config/ui_settings.yaml**: choices/readonly/labels для всех дропдаунов (Connection, Chat, LLM Settings) + список моделей по провайдерам
- **config/prompts.yaml**: системный промпт чата вынесен сюда (приоритет: `.env LLM_SYSTEM_PROMPT` → `prompts.yaml` → встроенный fallback)
- **.env**: добавлены LLM_TEMP_MIN/MAX/STEP, LLM_MAXTOKENS_MIN/MAX, CHAT_MAX_TOOL_ITERATIONS, CHAT_TOOL_RESULT_TRUNCATE, SQL_MAX_ROWS_DISPLAY, SCHEMA_MAX_SCHEMAS, TOP_QUERIES_*, EXPLAIN_*, DB_POOL_* — все с описаниями прямо в файле
- **THEME из .env теперь реально применяется** (`app.launch(theme=THEME)`), раньше было захардкожено `"default"`
- **Попутно исправлен баг** в `get_top_queries()`: был нерабочий плейсхолдер `LIMIT $1` без передачи параметра — теперь лимит подставляется корректно

## Проверка после установки
1. Создайте папку `config` в корне проекта, если её нет
2. Замените 4 файла по путям из таблицы выше
3. Проверьте/перенесите секреты в `.env` (см. предупреждение выше)
4. Перезапустите сервер
5. На вкладке Connection: дропдаун `Database` должен открываться только по клику (без ручного ввода произвольного текста)
6. На вкладке LLM Settings: слайдер Temperature должен иметь границы из `.env` (по умолчанию 0–2)

## Этап 2 — что добавилось
| Файл в архиве | Куда положить |
|---|---|
| `src/postgres_mcp/autonomous/llm_client.py` | заменить |
| `src/postgres_mcp/autonomous/mcp_server.py` | заменить |
| `src/postgres_mcp/autonomous/connection_store.py` | заменить (содержимое идентично вашему — включён для целостности пакета, реальных изменений нет) |
| `.env` | обновлён (v1.2.0) — добавлены OPENAI_BASE_URL/ANTHROPIC_BASE_URL/GOOGLE_BASE_URL, ANTHROPIC_API_VERSION, LLM_HTTP_TIMEOUT. **Снова перенесите свои реальные ключи/пароль перед заменой.** |
| `src/postgres_mcp/autonomous/app.py` | обновлён (v1.2.0) — см. ниже |

### Исправлен функциональный баг
Слайдер **Temperature** и поле **Max Tokens** на вкладке LLM Settings раньше сохранялись в `.env`, но реально не влияли на ответы в чате — `chat_fn()` не передавал их в `LLMClient.chat()`. Теперь передаёт (значения читаются из `.env` при каждом сообщении, так что "Save All to .env" применяется сразу, без перезапуска сервера).

### mcp_server.py
- `.env` теперь ищется по абсолютному пути (важно, если Claude Desktop/другой MCP-клиент запускает `mcp_server.py` как отдельный процесс не из корня проекта)
- `explain_query`-инструмент использует общие `EXPLAIN_FORMAT`/`EXPLAIN_ANALYZE` вместо своего хардкода
- ⚠️ Если когда-нибудь смените `EXPLAIN_FORMAT` на что-то, кроме `JSON` — разбор результата в этом инструменте (и в `pg_client.explain_query()`) перестанет работать корректно, оба места рассчитаны именно на JSON-вывод

## Не хватает / нужно уточнить для следующего этапа
- `requirements.txt` / `pyproject.toml` — не проверялись повторно, изменений зависимостей не потребовалось
- Если понадобится следующий этап — сообщите, буду отталкиваться от `PROJECT_STATUS.md`
