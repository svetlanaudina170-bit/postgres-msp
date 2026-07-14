Postgres MCP Project
Project Structure
postgres-mcp/
├── src/
│   ├── mcp_config.json
│   ├── postgres_mcp/
│   │   ├── __init__.py
│   │   ├── artifacts.py
│   │   ├── host-latest.py
│   │   ├── server.py
│   │   ├── .env
│   │   ├── database_health/
│   │   │   ├── __init__.py
│   │   │   ├── buffer_health_calc.py
│   │   │   ├── connection_health_calc.py
│   │   │   ├── constraint_health_calc.py
│   │   │   ├── database_health.py
│   │   │   ├── index_health_calc.py
│   │   │   ├── init.sql
│   │   │   ├── replication_calc.py
│   │   │   ├── sequence_health_calc.py
│   │   │   ├── vacuum_health_calc.py
│   │   ├── explain/
│   │   │   ├── __init__.py
│   │   │   ├── explain_plan.py
│   │   │   ├── README.md
│   │   ├── index/
│   │   │   ├── __init__.py
│   │   │   ├── dta_calc.py
│   │   │   ├── index_opt_base.py
│   │   │   ├── llm_opt.py
│   │   │   ├── presentation.py
│   │   ├── sql/
│   │   │   ├── __init__.py
│   │   │   ├── bind_params.py
│   │   │   ├── extension_utils.py
│   │   │   ├── index.py
│   │   │   ├── safe_sql.py
│   │   │   ├── sql_driver.py
│   │   ├── top_queries/
│   │   │   ├── __init__.py
│   │   │   ├── top_queries_calc.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .gitignore
└── README.md

Setup Instructions

Prerequisites:

Docker and Docker Compose installed
Python 3.12 (optional for local development)


Environment Variables:

Create a .env file in src/postgres_mcp/ with the database connection string:DATABASE_URL=postgresql://your_user:your_password@db:5432/your_db


Alternatively, set the environment variable:export DATABASE_URL=postgresql://your_user:your_password@db:5432/your_db




Build and Run:
docker-compose up --build


Access the Application:

The server runs on http://localhost:5001
PostgreSQL is available on localhost:5432



Development

Source code is in the src/ directory.
Configuration is stored in mcp_config.json.
Add dependencies to requirements.txt.
The server is started with python -m postgres_mcp.server.

Notes

The PostgreSQL data is persisted in the postgres_data volume.
The init.sql script in database_health/ is executed during database initialization.
The .env file in src/postgres_mcp/ is used to load environment variables.
Modify docker-compose.yml for additional configuration as needed.
