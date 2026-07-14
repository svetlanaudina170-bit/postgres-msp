# 📘 Postgres MCP Pro — сервер MCP для PostgreSQL

<img src="assets/postgres-mcp-pro.png" alt="Postgres MCP Pro Logo" width="600"/>

[![Лицензия: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Версия PyPI](https://img.shields.io/pypi/v/postgres-mcp)](https://pypi.org/project/postgres-mcp/)
[![Discord](https://img.shields.io/discord/1336769798603931789?label=Discord)](https://discord.gg/4BEHC7ZM)
[![Twitter Follow](https://img.shields.io/twitter/follow/auto_dba?style=flat)](https://x.com/auto_dba)
[![Contributors](https://img.shields.io/github/contributors/crystaldba/postgres-mcp)](https://github.com/crystaldba/postgres-mcp/graphs/contributors)

---

## 🔎 Обзор

**Postgres MCP Pro** — это open-source сервер **Model Context Protocol (MCP)**, предназначенный для помощи разработчикам и AI-агентам на всех этапах разработки: от начального кода и тестирования до деплоя и продакшн-оптимизации.

Отличается от простого подключения к базе данных следующими возможностями:

* **Анализ состояния БД**: индекс, буферный кэш, autovacuum, последовательности, репликация и др.
* **Оптимизация индексов**: автоматический подбор лучших индексов с помощью промышленных алгоритмов.
* **Планы выполнения**: EXPLAIN и симуляция с гипотетическими индексами.
* **Интеллект схемы**: генерация SQL с учётом структуры базы.
* **Безопасное выполнение SQL**: поддержка режима только для чтения и защита в продакшне.

Поддерживает транспорты: **stdio** и **SSE**.

[Запуск проекта и причины его создания](https://www.crystaldba.ai/blog/post/announcing-postgres-mcp-server-pro)

---

## 📺 Демонстрация

**От медленного к молниеносному**
AI сгенерировал приложение на SQLAlchemy ORM — но оно было слишком медленным.
Postgres MCP Pro с Cursor решил проблему за считанные минуты.

* 🚀 Оптимизация ORM-запросов, индексации и кэширования
* 🛠️ Исправление сломанной страницы
* 🧠 Улучшение вывода "топ-фильмов" путём анализа данных и корректировки запросов

👉 Подробнее: [movie-app.md](examples/movie-app.md)

---

## ⚡ Быстрый старт

### Требования:

1. Доступ к вашей базе данных PostgreSQL
2. Docker *или* Python 3.12+

#### Удостоверьтесь в доступе:

Пример — подключение через `psql` или [pgAdmin](https://www.pgadmin.org/)

---

### Установка

#### 🐳 Docker

```bash
docker pull crystaldba/postgres-mcp
```

#### 🐍 Python (через `pipx`)

```bash
pipx install postgres-mcp
```

или через `uv`:

```bash
uv pip install postgres-mcp
```

---

## ⚙️ Настройка AI-ассистента (на примере Claude Desktop)

Откройте конфигурационный файл:

* **MacOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
* **Windows**: `%APPDATA%/Claude/claude_desktop_config.json`

### Пример конфигурации:

#### Через Docker

```json
{
  "mcpServers": {
    "postgres": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm", "-e", "DATABASE_URI",
        "crystaldba/postgres-mcp", "--access-mode=unrestricted"
      ],
      "env": {
        "DATABASE_URI": "postgresql://username:password@localhost:5432/dbname"
      }
    }
  }
}
```

#### Через `pipx`

```json
{
  "mcpServers": {
    "postgres": {
      "command": "postgres-mcp",
      "args": ["--access-mode=unrestricted"],
      "env": {
        "DATABASE_URI": "postgresql://username:password@localhost:5432/dbname"
      }
    }
  }
}
```

#### Через `uv`

```json
{
  "mcpServers": {
    "postgres": {
      "command": "uv",
      "args": [
        "run", "postgres-mcp", "--access-mode=unrestricted"
      ],
      "env": {
        "DATABASE_URI": "postgresql://username:password@localhost:5432/dbname"
      }
    }
  }
}
```

#### Режимы доступа:

* `--access-mode=unrestricted`: полный доступ (dev)
* `--access-mode=restricted`: только чтение (prod)

---

## 🔄 SSE Transport

Чтобы использовать SSE:

```bash
docker run -p 8000:8000 \
  -e DATABASE_URI=postgresql://username:password@localhost:5432/dbname \
  crystaldba/postgres-mcp --access-mode=unrestricted --transport=sse
```

Пример для Cursor:

```json
{
  "mcpServers": {
    "postgres": {
      "type": "sse",
      "url": "http://localhost:8000/sse"
    }
  }
}
```

---

## 🧩 Установка расширений (опционально)

Нужно для:

* `pg_stat_statements` — для анализа запросов
* `hypopg` — симуляция индексов

```sql
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;
CREATE EXTENSION IF NOT EXISTS hypopg;
```

---

## 🧪 Примеры использования

* **Проверка БД**: "Check the health of my database..."
* **Медленные запросы**: "What are the slowest queries..."
* **Рекомендации**: "How can I make it faster?"
* **Индексы**: "Suggest indexes to improve performance"
* **Оптимизация запроса**: "Help me optimize this query: SELECT ..."

---

## 📡 MCP API (интерфейс)

Сервер предоставляет **MCP tools**:

| Tool                       | Назначение                          |
| -------------------------- | ----------------------------------- |
| `list_schemas`             | Список схем БД                      |
| `list_objects`             | Список таблиц, представлений и т.п. |
| `get_object_details`       | Подробности по объекту              |
| `execute_sql`              | Выполнение SQL (в т.ч. безопасно)   |
| `explain_query`            | EXPLAIN план запроса                |
| `get_top_queries`          | Самые медленные запросы             |
| `analyze_workload_indexes` | Анализ всех запросов                |
| `analyze_query_indexes`    | Анализ конкретных запросов          |
| `analyze_db_health`        | Здоровье БД по множеству метрик     |

---

## 📌 Отличия от других MCP-серверов

| Postgres MCP Pro                  | Другие MCP-серверы      |
| --------------------------------- | ----------------------- |
| ✅ Проверки здоровья с гарантией   | ❌ Генерация LLM         |
| ✅ Оптимизация индексов алгоритмом | ❌ Гипотетические советы |
| ✅ Симуляции EXPLAIN               | ❌ "Попробуй сам"        |
| ✅ Детальный workload-анализ       | ❌ Нет анализа запросов  |

---

## 🧠 Почему нужны инструменты MCP?

LLM отлично справляется с генерацией SQL, но медленно, дорого и непредсказуемо.
Оптимизация БД давно решается алгоритмами.
MCP Pro сочетает лучшее от LLM и классических алгоритмов.

---

## 🛠️ Технические заметки (ключевые моменты)

* **Индексы**: использование `pg_stat_statements`, генерация кандидатов, анализ через `hypopg`
* **LLM-оптимизация**: экспериментальная, с использованием OpenAI API (`OPENAI_API_KEY`)
* **Здоровье БД**: адаптация проверок из PgHero
* **Библиотека подключения**: `psycopg3` с `libpq`
* **Безопасность SQL**: чтение, защита от `ROLLBACK; DROP ...`
* **Интеграция со схемой**: передаёт схему агенту через инструменты, а не ресурсы
* **Конфигурация соединений**: через переменные среды
* **Dev-сборка**: `uv`, `pip`, запуск с локальной БД
