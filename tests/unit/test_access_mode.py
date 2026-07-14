from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from postgres_mcp.server import AccessMode
from postgres_mcp.server import get_sql_driver
from postgres_mcp.sql.safe_sql import SafeSqlDriver
from postgres_mcp.sql.sql_driver import SqlDriver


@pytest.fixture
def mock_db_connection():
    """Mock database connection pool."""
    conn = MagicMock()
    conn._is_valid = True
    conn.pool_connect = AsyncMock()
    return conn


@pytest.mark.parametrize(
    "access_mode,expected_driver_type",
    [
        (AccessMode.UNRESTRICTED.value, SqlDriver),
        (AccessMode.RESTRICTED.value, SafeSqlDriver),
    ],
)
@pytest.mark.asyncio
async def test_get_sql_driver_returns_correct_driver(access_mode, expected_driver_type, mock_db_connection):
    """Test that get_sql_driver returns the correct driver type based on access mode."""
    with patch("postgres_mcp.server.db_connection", mock_db_connection):
        driver = await get_sql_driver(database_url="postgresql://user:pass@localhost/db", access_mode=access_mode)
        assert isinstance(driver, expected_driver_type)

        # When in RESTRICTED mode, verify timeout is set
        if access_mode == AccessMode.RESTRICTED.value:
            assert isinstance(driver, SafeSqlDriver)
            assert driver.timeout == 30


@pytest.mark.asyncio
async def test_get_sql_driver_sets_timeout_in_restricted_mode(mock_db_connection):
    """Test that get_sql_driver sets the timeout in restricted mode."""
    with patch("postgres_mcp.server.db_connection", mock_db_connection):
        driver = await get_sql_driver(
            database_url="postgresql://user:pass@localhost/db", access_mode=AccessMode.RESTRICTED.value
        )
        assert isinstance(driver, SafeSqlDriver)
        assert driver.timeout == 30
        assert hasattr(driver, "sql_driver")


@pytest.mark.asyncio
async def test_get_sql_driver_in_unrestricted_mode_no_timeout(mock_db_connection):
    """Test that get_sql_driver in unrestricted mode is a regular SqlDriver."""
    with patch("postgres_mcp.server.db_connection", mock_db_connection):
        driver = await get_sql_driver(
            database_url="postgresql://user:pass@localhost/db", access_mode=AccessMode.UNRESTRICTED.value
        )
        assert isinstance(driver, SqlDriver)
        assert not hasattr(driver, "timeout")
