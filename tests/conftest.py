import asyncio
from typing import Generator

import pytest
from dotenv import load_dotenv

from postgres_mcp.sql import reset_postgres_version_cache

load_dotenv()

try:
    from utils import create_postgres_container

    _HAS_DOCKER_UTILS = True
except (ImportError, ModuleNotFoundError):
    _HAS_DOCKER_UTILS = False


# Define a custom event loop policy that handles cleanup better
@pytest.fixture(scope="session")
def event_loop_policy():
    """Create and return a custom event loop policy for tests."""
    return asyncio.DefaultEventLoopPolicy()


if _HAS_DOCKER_UTILS:

    @pytest.fixture(scope="class", params=["postgres:15", "postgres:16"])
    def test_postgres_connection_string(request) -> Generator[tuple[str, str], None, None]:
        yield from create_postgres_container(request.param)

else:

    @pytest.fixture(scope="class")
    def test_postgres_connection_string():
        pytest.skip("docker module not available")


@pytest.fixture(autouse=True)
def reset_pg_version_cache():
    """Reset the PostgreSQL version cache before each test."""
    reset_postgres_version_cache()
    yield
