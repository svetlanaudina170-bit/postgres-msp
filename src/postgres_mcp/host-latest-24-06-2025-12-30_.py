import asyncio
import json
import logging
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
import aiohttp
import openai
import requests
from dotenv import load_dotenv
import os
from postgres_mcp.sql import obfuscate_password

# Загрузим ключи из .env
load_dotenv()

# Настройка логирования
logger = logging.getLogger(__name__)

# Получение параметров логирования и PostgreSQL из .env
log_level = os.getenv("LOG_LEVEL", "INFO").upper()
log_to_file = os.getenv("LOG_TO_FILE", "false").lower() == "true"
logs_path = os.getenv("LOGS_PATH", "./logs")
host_log_file = os.getenv("HOST_LOG_FILE", "host.log")
user_id = os.getenv("USER_ID", "default")
server_log_file = os.getenv("SERVER_LOG_FILE", "server") + f"_{user_id}.log"
access_mode = os.getenv("ACCESS_MODE", "unrestricted")

# Формирование DATABASE_URL из отдельных переменных
pg_user = os.getenv("PG_USER")
pg_password = os.getenv("PG_PASSWORD")
pg_host = os.getenv("PG_HOST")
pg_port = os.getenv("PG_PORT")
pg_database = os.getenv("PG_DATABASE")

if not all([pg_user, pg_password, pg_host, pg_port, pg_database]):
    logger.error("Отсутствуют необходимые параметры PostgreSQL в .env (PG_USER, PG_PASSWORD, PG_HOST, PG_PORT, PG_DATABASE)")
    raise ValueError("Не удалось сформировать DATABASE_URL: отсутствуют необходимые параметры")

database_url = f"postgresql://{pg_user}:{pg_password}@{pg_host}:{pg_port}/{pg_database}"
logger.info(f"Сформирована строка подключения: {obfuscate_password(database_url)}")

# Установка уровня логирования
logging.basicConfig(level=getattr(logging, log_level, logging.INFO))

# Если логирование в файл включено, добавляем FileHandler
if log_to_file:
    os.makedirs(logs_path, exist_ok=True)
    log_file_path = os.path.join(logs_path, host_log_file)
    file_handler = logging.FileHandler(log_file_path, encoding="utf-8")
    file_handler.setLevel(getattr(logging, log_level, logging.INFO))
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


@dataclass
class ModelConfig:
    """Конфигурация модели LLM"""

    name: str
    provider: str
    api_key: str
    base_url: Optional[str] = None
    model_name: str = ""
    supports_tools: bool = True
    system_prompt: Optional[str] = None


@dataclass
class MCPServerConfig:
    """Конфигурация MCP сервера"""

    name: str
    url: str
    transport: str = "sse"


class MCPClient:
    """Клиент для работы с MCP сервером"""

    def __init__(self, server_url: str):
        self.server_url = server_url
        self.session = None
        self.request_id = 0
        self.initialized = False

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=25))
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            if self.session:
                await self.session.close()
        except Exception as e:
            logger.error(f"Ошибка закрытия сессии: {e}")

    def _get_next_id(self):
        """Получить следующий ID для запроса"""
        self.request_id += 1
        return self.request_id

    async def _make_request(self, method: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Выполнить MCP запрос"""
        request_data = {"jsonrpc": "2.0", "id": self._get_next_id(), "method": method}

        if params:
            request_data["params"] = params

        logger.debug(f"Выполнение MCP запроса: {method}")

        try:
            async with self.session.post(self.server_url, json=request_data, headers={"Content-Type": "application/json"}) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Ошибка HTTP {response.status}: {error_text}")
                    return {"error": f"HTTP {response.status}: {error_text}"}

                response_data = await response.json()
                logger.debug(f"Ответ MCP: {response_data}")

                if "error" in response_data and response_data["error"] is not None:
                    logger.error(f"Ошибка MCP: {response_data['error']}")
                    return response_data

                return response_data

        except Exception as e:
            logger.error(f"Ошибка запроса: {e}")
            return {"error": str(e)}

    async def initialize(self) -> bool:
        """Инициализация MCP соединения"""
        logger.info("Инициализация MCP соединения...")

        logging_config = {"logToFile": log_to_file, "logLevel": log_level, "logFile": server_log_file, "logsPath": logs_path}

        response = await self._make_request(
            "initialize",
            {
                "protocolVersion": "2024-11-12",
                "capabilities": {"tools": {}, "loggingConfig": logging_config},
                "clientInfo": {"name": "MultiModelMCPClient", "version": "1.0.0"},
            },
        )

        if "error" in response:
            logger.error(f"Ошибка инициализации: {response['error']}")
            return False

        self.initialized = True
        logger.info("MCP соединение успешно инициализировано")
        return True

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Получить список доступных инструментов"""
        if not self.initialized:
            if not await self.initialize():
                return []

        logger.info("Запрос списка инструментов...")
        response = await self._make_request("tools/list")

        if "error" in response:
            logger.error(f"Ошибка получения списка инструментов: {response['error']}")
            return []

        tools = response.get("result", {}).get("tools", [])
        logger.info(f"Получено инструментов: {len(tools)}")
        return tools

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Вызвать инструмент MCP сервера"""
        if not self.initialized:
            if not await self.initialize():
                return {"error": "MCP не инициализирован"}

        log_args = arguments.copy()
        if tool_name == "send_email":
            log_args = {k: v for k, v in arguments.items() if k in ["to_email", "subject", "body"]}

        logger.info(f"Вызов инструмента: {tool_name} с аргументами: {log_args}")

        response = await self._make_request("tools/call", {"name": tool_name, "arguments": arguments})

        if "error" in response:
            logger.error(f"Ошибка вызова инструмента: {response['error']}")
            return response

        result = response.get("result", {})
        logger.info(f"Инструмент {tool_name} успешно выполнен")
        return result


class MultiModelMCPClient:
    """Мультимодельный MCP клиент с улучшенной архитектурой"""

    def __init__(self):
        self.models: Dict[str, ModelConfig] = {}
        self.mcp_servers: Dict[str, MCPServerConfig] = {}
        self.conversation_history: List[Dict[str, Any]] = []
        self.available_tools: List[Dict[str, Any]] = []
        self.tool_server_mapping: Dict[str, str] = {}
        self.database_url: str = database_url
        self.access_mode: str = access_mode

    def add_model(self, config: ModelConfig):
        """Добавить модель"""
        self.models[config.name] = config
        logger.info(f"Добавлена модель: {config.name} ({config.provider})")

    def add_mcp_server(self, config: MCPServerConfig):
        """Добавить MCP сервер"""
        self.mcp_servers[config.name] = config
        logger.info(f"Добавлен MCP сервер: {config.name} по адресу {config.url}")

    async def test_mcp_server(self, server_name: str) -> bool:
        """Тестирование подключения к MCP серверу"""
        if server_name not in self.mcp_servers:
            logger.error(f"Сервер {server_name} не найден")
            return False

        server_config = self.mcp_servers[server_name]

        try:
            async with MCPClient(server_config.url) as mcp:
                success = await mcp.initialize()
                if success:
                    tools = await mcp.list_tools()
                    logger.info(f"Тест сервера {server_name} успешен, доступно инструментов: {len(tools)}")
                    return True
                else:
                    logger.error(f"Инициализация сервера {server_name} не удалась")
                    return False
        except Exception as e:
            logger.error(f"Ошибка тестирования сервера {server_name}: {e}")
            return False

    async def test_all_servers(self) -> str:
        """Тестирование всех MCP серверов"""
        results = []
        for server_name in self.mcp_servers.keys():
            success = await self.test_mcp_server(server_name)
            status = "✓ Онлайн" if success else "✗ Оффлайн"
            results.append(f"{server_name}: {status}")

        return "Статус MCP серверов:\n" + "\n".join(results)

    async def initialize_mcp_servers(self):
        """Инициализация всех MCP серверов"""
        all_tools = []
        self.tool_server_mapping.clear()

        for server_name, server_config in self.mcp_servers.items():
            logger.info(f"Инициализация MCP сервера: {server_name}")

            try:
                async with MCPClient(server_config.url) as mcp:
                    success = await mcp.initialize()
                    if not success:
                        logger.error(f"Не удалось инициализировать сервер: {server_name}")
                        continue

                    tools = await mcp.list_tools()
                    for tool in tools:
                        tool["_server"] = server_name
                        self.tool_server_mapping[tool["name"]] = server_name

                    all_tools.extend(tools)
                    logger.info(f"Загружено инструментов с сервера {server_name}: {len(tools)}")

            except Exception as e:
                logger.error(f"Ошибка инициализации сервера {server_name}: {e}")

        self.available_tools = all_tools
        logger.info(f"Всего загружено инструментов: {len(self.available_tools)}")

        for tool in self.available_tools:
            logger.info(f"Доступный инструмент: {tool['name']} с сервера {tool.get('_server', 'неизвестно')}")

    def format_tools_for_openai(self) -> List[Dict[str, Any]]:
        """Форматирование инструментов для API"""
        formatted_tools = []
        for tool in self.available_tools:
            # Исключаем database_url и access_mode из схемы параметров для PostgreSQL-инструментов
            input_schema = tool.get("inputSchema", {"type": "object", "properties": {}, "required": []})
            if "properties" in input_schema:
                properties = input_schema["properties"].copy()
                properties.pop("database_url", None)
                properties.pop("access_mode", None)
                input_schema["properties"] = properties
                required = input_schema.get("required", []).copy()
                if "database_url" in required:
                    required.remove("database_url")
                if "access_mode" in required:
                    required.remove("access_mode")
                input_schema["required"] = required

            formatted_tool = {
                "type": "function",
                "function": {"name": tool["name"], "description": tool.get("description", ""), "parameters": input_schema},
            }
            formatted_tools.append(formatted_tool)
        return formatted_tools

    async def call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        """Вызов MCP инструмента с добавлением PostgreSQL параметров"""
        server_name = self.tool_server_mapping.get(tool_name)

        if not server_name or server_name not in self.mcp_servers:
            return f"Ошибка: сервер для инструмента {tool_name} не найден"

        server_config = self.mcp_servers[server_name]

        # Добавляем database_url и access_mode для инструментов PostgreSQL
        postgres_tools = [
            "list_schemas",
            "list_objects",
            "get_object_details",
            "explain_query",
            "execute_sql",
            "analyze_workload_indexes",
            "analyze_query_indexes",
            "analyze_db_health",
            "get_top_queries",
        ]
        if tool_name in postgres_tools:
            arguments["database_url"] = self.database_url
            arguments["access_mode"] = self.access_mode
            log_args = arguments.copy()
            log_args["database_url"] = obfuscate_password(self.database_url)
        else:
            log_args = arguments.copy()

        logger.info(f"Вызов инструмента: {tool_name} с аргументами: {log_args}")

        try:
            async with MCPClient(server_config.url) as mcp:
                tool_result = await mcp.call_tool(tool_name, arguments)
                if "error" in tool_result:
                    logger.error(f"Ошибка вызова инструмента: {tool_result['error']}")
                    return f"Ошибка: {tool_result['error']['message']}"

                # Обработка ответа в формате ResponseType (список словарей)
                if "content" in tool_result and tool_result["content"]:
                    content = tool_result["content"]
                    if isinstance(content, list) and len(content) > 0 and "text" in content[0]:
                        return content[0]["text"]
                    return json.dumps(content, ensure_ascii=False, indent=2)
                return json.dumps(tool_result, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка вызова инструмента {tool_name}: {e}")
            return f"Ошибка: {str(e)}"

    async def chat_with_openai(self, model_name: str, message: str) -> str:
        """Чат с OpenAI-совместимой моделью"""
        if model_name not in self.models:
            return f"Модель {model_name} не найдена"

        model_config = self.models[model_name]

        client_kwargs = {
            "api_key": model_config.api_key,
        }
        if model_config.base_url:
            client_kwargs["base_url"] = model_config.base_url

        client = openai.AsyncOpenAI(**client_kwargs)

        system_prompt = model_config.system_prompt or (
            "Ты полезный ассистент с доступом к инструментам PostgreSQL. Используй инструменты, такие как list_schemas или execute_sql, "
            "когда это необходимо, и следуй их схемам параметров."
        )

        messages = [{"role": "system", "content": system_prompt}]

        recent_history = self.conversation_history[-10:] if len(self.conversation_history) > 10 else self.conversation_history
        messages.extend(recent_history)
        messages.append({"role": "user", "content": message})

        try:
            tools = self.format_tools_for_openai()
            logger.info(f"Доступные инструменты для {model_name}: {[t['function']['name'] for t in tools]}")

            response = await client.chat.completions.create(
                model=model_config.model_name,
                messages=messages,
                tools=tools if model_config.supports_tools and tools else None,
                tool_choice="auto" if model_config.supports_tools and tools else None,
                temperature=0.7,
                max_tokens=1000,
            )

            response_message = response.choices[0].message

            if response_message.tool_calls:
                logger.info(f"Модель запросила вызовов инструментов: {len(response_message.tool_calls)}")

                messages.append(
                    {
                        "role": "assistant",
                        "content": response_message.content,
                        "tool_calls": [
                            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                            for tc in response_message.tool_calls
                        ],
                    }
                )

                for tool_call in response_message.tool_calls:
                    tool_name = tool_call.function.name
                    try:
                        tool_args = json.loads(tool_call.function.arguments)
                    except json.JSONDecodeError as e:
                        logger.error(f"Ошибка парсинга аргументов инструмента: {e}")
                        tool_args = {}

                    if tool_name == "send_email":
                        smtp_params = {
                            "smtp_server": os.getenv("SMTP_SERVER"),
                            "smtp_port": os.getenv("SMTP_PORT"),
                            "sender_email": os.getenv("EMAIL_ADDRESS"),
                            "password": os.getenv("EMAIL_PASSWORD"),
                        }
                        missing_params = [k for k, v in smtp_params.items() if not v]
                        if missing_params:
                            return f"Ошибка: отсутствуют параметры SMTP в .env: {', '.join(missing_params)}"

                        tool_args.update(smtp_params)

                        print(f"\nПодтвердите отправку письма:")
                        print(f"Получатель: {tool_args.get('to_email')}")
                        print(f"Тема: {tool_args.get('subject')}")
                        print(f"Тело: {tool_args.get('body')[:100]}{'...' if len(tool_args.get('body', '')) > 100 else ''}")
                        confirmation = input("Отправить письмо? да/нет (yes/no): ").strip().lower()
                        if confirmation not in ["да", "yes"]:
                            return "Отправка письма отменена пользователем"

                    tool_result = await self.call_mcp_tool(tool_name, tool_args)

                    messages.append({"tool_call_id": tool_call.id, "role": "tool", "name": tool_name, "content": tool_result})

                final_response = await client.chat.completions.create(
                    model=model_config.model_name, messages=messages, temperature=0.7, max_tokens=1000
                )

                assistant_message = final_response.choices[0].message.content
            else:
                assistant_message = response_message.content

            self.conversation_history.append({"role": "user", "content": message})
            self.conversation_history.append({"role": "assistant", "content": assistant_message})

            return assistant_message

        except Exception as e:
            logger.error(f"Ошибка вызова модели {model_name}: {e}")
            return f"Ошибка: {str(e)}"

    def chat_with_gigachat(self, message: str) -> str:
        """Чат с GigaChat"""
        if "gigachat" not in self.models:
            return "Модель GigaChat не настроена"

        model_config = self.models["gigachat"]

        try:
            headers = {"Authorization": f"Bearer {model_config.api_key}", "Content-Type": "application/json"}

            system_prompt = model_config.system_prompt or (
                "Ты полезный ассистент с доступом к инструментам PostgreSQL. "
                "Для запросов, связанных с базами данных, используй инструменты, такие как list_schemas или execute_sql, "
                "и следуй их схемам параметров."
            )

            messages = [{"role": "system", "content": system_prompt}]
            recent_history = self.conversation_history[-8:] if len(self.conversation_history) > 8 else self.conversation_history
            messages.extend(recent_history)
            messages.append({"role": "user", "content": message})

            tools = self.format_tools_for_openai()
            logger.info(f"Отправка инструментов в GigaChat: {[t['function']['name'] for t in tools]}")
            logger.debug(f"Схема инструментов для GigaChat: {json.dumps(tools, ensure_ascii=False, indent=2)}")

            payload = {
                "model": model_config.model_name,
                "messages": messages,
                "temperature": 0.7,
                "max_tokens": 1000,
                "tools": tools if model_config.supports_tools and tools else None,
                "tool_choice": "auto" if model_config.supports_tools and tools else None,
            }

            logger.debug(f"Отправка запроса в GigaChat: {json.dumps(payload, ensure_ascii=False, indent=2)}")

            response = requests.post(model_config.base_url, headers=headers, json=payload, timeout=30, verify=False)

            if response.status_code == 200:
                data = response.json()
                logger.debug(f"Ответ GigaChat: {json.dumps(data, ...)}")
                response_message = data["choices"][0]["message"]

                if "tool_calls" in response_message and response_message["tool_calls"]:
                    logger.info(f"GigaChat запросил вызовов инструментов: {len(response_message['tool_calls'])}")
                    messages.append(response_message)

                    for tool_call in response_message["tool_calls"]:
                        tool_name = tool_call["function"]["name"]
                        try:
                            tool_args = json.loads(tool_call["function"]["arguments"])
                        except json.JSONDecodeError as e:
                            logger.error(f"Ошибка парсинга аргументов инструмента: {e}")
                            tool_args = []

                        if tool_name == "send_email":
                            smtp_params = {
                                "smtp_server": os.getenv("SMTP_SERVER"),
                                "smtp_port": os.getenv("SMTP_PORT"),
                                "sender_email": os.getenv("EMAIL_ADDRESS"),
                                "password": os.getenv("EMAIL_PASSWORD"),
                            }
                            missing_params = [k for k, v in smtp_params.items() if not v]
                            if missing_params:
                                return f"Ошибка: отсутствуют параметры SMTP в .env: {', '.join(missing_params)}"

                            tool_args.update(smtp_params)

                            print(f"\nПодтвердите отправку письма:")
                            print(f"Получатель: {tool_args.get('to_email')}")
                            print(f"Тема: {tool_args.get('subject')}")
                            print(f"Тело: {tool_args.get('body')[:100]}{'...' if len(tool_args.get('body', '')) > 100 else ''}")
                            confirmation = input("Отправить письмо? да/нет (yes/no): ").strip().lower()
                            if confirmation not in ["да", "yes"]:
                                return "Отправка письма отменена пользователем"

                        log_args = tool_args.copy()
                        if tool_name == "send_email":
                            log_args = {k: v for k, v in tool_args.items() if k in ["to_email", "subject", "body"]}

                        logger.info(f"Вызов инструмента: {tool_name} с аргументами: {log_args}")
                        tool_result = asyncio.run(self.call_mcp_tool(tool_name, tool_args))

                        messages.append({"role": "tool", "tool_call_id": tool_call["id"], "name": tool_name, "content": tool_result})

                    payload["messages"] = messages
                    final_response = requests.post(model_config.base_url, headers=headers, json=payload, timeout=30)

                    if final_response.status_code == 200:
                        final_data = final_response.json()
                        logger.debug(f"Финальный ответ GigaChat: {json.dumps(final_data, ...)}")
                        assistant_message = final_data["choices"][0]["message"]["content"]
                    else:
                        return f"Ошибка API GigaChat: {response.status_code} - {response.text}"
                else:
                    assistant_message = response_message.get("content", "")
                    logger.info("GigaChat не запросил вызов инструментов")

                self.conversation_history.append({"role": "user", "content": message})
                self.conversation_history.append({"role": "assistant", "content": assistant_message})

                return assistant_message
            else:
                return f"Ошибка API GigaChat: {response.status_code} - {response.text}"

        except Exception as e:
            logger.error(f"Ошибка вызова GigaChat: {e}")
            return f"Ошибка: {str(e)}"

    def clear_history(self):
        """Очистить историю разговора"""
        self.conversation_history.clear()
        logger.info("История разговора очищена")

    def show_available_tools(self) -> str:
        """Показать список доступных инструментов"""
        if not self.available_tools:
            return "Инструменты не доступны. Убедитесь, что MCP серверы запущены и инициализированы."

        tools_info = "Доступные инструменты:\n"
        for i, tool in enumerate(self.available_tools, 1):
            server = tool.get("_server", "неизвестно")
            tools_info += f"{i}. {tool['name']} (с сервера {server}): {tool.get('description', 'Описание отсутствует')}\n"
        return tools_info


async def main():
    """Основная функция с интерактивным диалогом"""
    client = MultiModelMCPClient()

    # Настройка моделей
    client.add_model(
        ModelConfig(
            name="openai-gpt4.1-nano", provider="openai", api_key=os.getenv("OPENAI_API_KEY"), model_name=os.getenv("LLM_MODEL"), supports_tools=True
        )
    )

    client.add_model(
        ModelConfig(
            name="gigachat",
            provider="gigachat",
            api_key=os.getenv("GIGACHAT_ACCESS_TOKEN"),
            base_url=os.getenv("GIGACHAT_BASE_URL"),
            model_name=os.getenv("GIGACHAT_MODEL_NAME"),
            supports_tools=True,
        )
    )

    client.add_model(
        ModelConfig(
            name="ollama-llama-server",
            provider="http",
            api_key="http://localhost:11434",
            base_url="http://localhost:11434/v1",
            model_name="llama-server",
            supports_type=True,
        )
    )

    # Загрузка MCP-серверов из mcp_config.json
    mcp_config_path = "mcp_config.json"
    if os.path.exists(mcp_config_path):
        with open(mcp_config_path, "r", encoding="utf-8") as f:
            mcp_config = json.load(f)
            for server_name, server_data in mcp_config["mcpServers"].items():
                client.add_mcp_server(
                    MCPServerConfig(name=server_name, url=server_data["transport"]["url"], transport=server_data["transport"]["type"])
                )
                logger.info(f"Загружен MCP сервер из конфигурации: {server_name}")
    else:
        logger.warning(f"Файл конфигурации MCP не найден по пути {mcp_config_path}, используется сервер по умолчанию")
        client.add_mcp_server(MCPServerConfig(name="postgres-mcp-tools-server", url="http://localhost:5000"))

    print("=== Мультимодельный MCP Клиент ===")
    print("Тестирование MCP серверов...")
    test_results = await client.test_all_servers()
    print(test_results)

    print("\nИнициализация MCP серверов...")
    await client.initialize_mcp_servers()

    print(f"\nДоступные модели: {list(client.models.keys())}")
    print(f"Доступные MCP серверы: {list(client.mcp_servers.keys())}")
    print(f"Всего загружено инструментов: {len(client.available_tools)}")

    print("\nКоманды:")
    print("- /model <model_name> - Переключить модель")
    print("- /tools - Показать доступные инструменты")
    print("- /test - Тестировать MCP серверы")
    print("- /clear - Очистить историю разговора")
    print("- /db <url> - Установить URL базы данных")
    print("- /access-mode <mode> - Установить режим доступа (unrestricted/restricted)")
    print("- /quit - Выйти")
    print("-" * 50)

    current_model = "openai-gpt4.1-nano"

    while True:
        try:
            user_input = input(f"\n[{current_model}] {user_id} (db: {obfuscate_password(client.database_url)}, mode: {client.access_mode}): ").strip()

            if not user_input:
                continue

            if user_input.startswith("/"):
                command_parts = user_input[1:].split(maxsplit=1)
                command = command_parts[0].lower()

                if command == "quit":
                    print("До свидания!")
                    break
                elif command == "clear":
                    client.clear_history()
                    print("История очищена!")
                elif command == "tools":
                    print(client.show_available_tools())
                elif command == "test":
                    print(await client.test_all_servers())
                elif command == "model" and len(command_parts) > 1:
                    new_model = command_parts[1]
                    if new_model in client.models:
                        current_model = new_model
                        print(f"Переключено на модель: {current_model}")
                    else:
                        print(f"Модель не найдена: {new_model}")
                        print("Доступные модели:", ", ".join(client.models.keys()))
                elif command == "db" and len(command_parts) > 1:
                    client.database_url = command_parts[1]
                    print(f"Установлен URL базы данных: {obfuscate_password(client.database_url)}")
                elif command == "access-mode" and len(command_parts) > 1:
                    new_mode = command_parts[1].lower()
                    if new_mode in ["unrestricted", "restricted"]:
                        client.access_mode = new_mode
                        print(f"Установлен режим доступа: {new_mode}")
                    else:
                        print("Недопустимый режим доступа: используйте 'unrestricted' или 'restricted'")
                else:
                    print("Неизвестная команда")
            else:
                print(f"\n[{current_model}] Ассистент: ", end="", flush=True)

                if current_model == "gigachat":
                    response = client.chat_with_gigachat(user_input)
                else:
                    response = await client.chat_with_openai(current_model, user_input)

                print(response)

        except KeyboardInterrupt:
            print("\nДо свидания!")
            break
        except Exception as e:
            print(f"Ошибка: {e}")


if __name__ == "__main__":
    asyncio.run(main())
