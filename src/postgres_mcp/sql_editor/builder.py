# =========================================================================
# VERSION: 1.0.0
# Path: src/postgres_mcp/sql_editor/builder.py
# SQL Query Builder — цепочечный построитель SQL-запросов разных типов
# =========================================================================

from typing import Optional


_STMT_TYPES = [
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE TABLE",
    "CREATE INDEX",
    "ALTER TABLE",
    "DROP TABLE",
    "TRUNCATE",
    "EXPLAIN SELECT",
    "MERGE",
    "WITH (CTE)",
]

_JOIN_TYPES = ["INNER JOIN", "LEFT JOIN", "RIGHT JOIN", "FULL JOIN", "CROSS JOIN"]


class SQLBuilder:
    """Цепочечный построитель SQL-запросов.

    Пример:
        sql = (SQLBuilder()
               .select("id", "name", "email")
               .from_table("public", "users")
               .where("age > 18")
               .order_by("name")
               .limit(100)
               .build())
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._type: str = "SELECT"
        self._schema: str = "public"
        self._table: str = ""
        self._columns: list[str] = []
        self._where: str = ""
        self._order_by: str = ""
        self._group_by: str = ""
        self._having: str = ""
        self._limit: Optional[int] = None
        self._offset: Optional[int] = None
        self._joins: list[tuple[str, str, str]] = []  # (type, table, on_clause)
        self._insert_columns: list[str] = []
        self._insert_values: str = ""
        self._set_clause: str = ""
        self._distinct: bool = False
        self._create_definition: str = ""
        self._index_columns: list[str] = ""
        self._index_name: str = ""
        self._index_unique: bool = False
        self._index_method: str = "btree"
        self._alter_action: str = ""
        self._cte_name: str = ""
        self._cte_query: str = ""
        self._merge_target: str = ""
        self._merge_using: str = ""
        self._merge_on: str = ""
        self._merge_matched: str = ""
        self._merge_not_matched: str = ""

    # --- Сеттеры ---

    def set_type(self, stmt_type: str) -> "SQLBuilder":
        self._type = stmt_type.upper()
        return self

    def select(self, *columns: str) -> "SQLBuilder":
        self._columns = list(columns) if columns else ["*"]
        self._type = "SELECT"
        return self

    def distinct(self, yes: bool = True) -> "SQLBuilder":
        self._distinct = yes
        return self

    def from_table(self, schema: str, table: str) -> "SQLBuilder":
        self._schema = schema
        self._table = table
        return self

    def where(self, clause: str) -> "SQLBuilder":
        self._where = clause
        return self

    def order_by(self, clause: str) -> "SQLBuilder":
        self._order_by = clause
        return self

    def group_by(self, clause: str) -> "SQLBuilder":
        self._group_by = clause
        return self

    def having(self, clause: str) -> "SQLBuilder":
        self._having = clause
        return self

    def limit(self, n: Optional[int]) -> "SQLBuilder":
        self._limit = n
        return self

    def offset(self, n: Optional[int]) -> "SQLBuilder":
        self._offset = n
        return self

    def add_join(self, join_type: str, table: str, on_clause: str) -> "SQLBuilder":
        self._joins.append((join_type.upper(), table, on_clause))
        return self

    def insert_into(self, schema: str, table: str) -> "SQLBuilder":
        self._schema = schema
        self._table = table
        self._type = "INSERT"
        return self

    def insert_columns(self, *columns: str) -> "SQLBuilder":
        self._insert_columns = list(columns)
        return self

    def insert_values(self, values: str) -> "SQLBuilder":
        self._insert_values = values
        return self

    def update_table(self, schema: str, table: str) -> "SQLBuilder":
        self._schema = schema
        self._table = table
        self._type = "UPDATE"
        return self

    def set_values(self, clause: str) -> "SQLBuilder":
        self._set_clause = clause
        return self

    def delete_from(self, schema: str, table: str) -> "SQLBuilder":
        self._schema = schema
        self._table = table
        self._type = "DELETE"
        return self

    def create_table(self, schema: str, table: str, definition: str) -> "SQLBuilder":
        self._schema = schema
        self._table = table
        self._type = "CREATE TABLE"
        self._create_definition = definition
        return self

    def create_index(
        self, schema: str, table: str, columns: str, name: str = "", unique: bool = False, method: str = "btree"
    ) -> "SQLBuilder":
        self._schema = schema
        self._table = table
        self._index_columns = columns
        self._index_name = name
        self._index_unique = unique
        self._index_method = method
        self._type = "CREATE INDEX"
        return self

    def alter_table(self, schema: str, table: str, action: str) -> "SQLBuilder":
        self._schema = schema
        self._table = table
        self._alter_action = action
        self._type = "ALTER TABLE"
        return self

    def drop_table(self, schema: str, table: str) -> "SQLBuilder":
        self._schema = schema
        self._table = table
        self._type = "DROP TABLE"
        return self

    def truncate(self, schema: str, table: str) -> "SQLBuilder":
        self._schema = schema
        self._table = table
        self._type = "TRUNCATE"
        return self

    def with_cte(self, name: str, query: str) -> "SQLBuilder":
        self._cte_name = name
        self._cte_query = query
        self._type = "WITH (CTE)"
        return self

    def merge(self, target: str, using: str, on_clause: str, matched: str, not_matched: str) -> "SQLBuilder":
        self._merge_target = target
        self._merge_using = using
        self._merge_on = on_clause
        self._merge_matched = matched
        self._merge_not_matched = not_matched
        self._type = "MERGE"
        return self

    # --- Построение SQL ---

    def _full_name(self) -> str:
        return f"{self._schema}.{self._table}" if self._schema and self._table else self._table

    def build(self) -> str:
        t = self._type

        if t == "SELECT":
            return self._build_select()
        elif t == "INSERT":
            return self._build_insert()
        elif t == "UPDATE":
            return self._build_update()
        elif t == "DELETE":
            return self._build_delete()
        elif t == "CREATE TABLE":
            return self._build_create_table()
        elif t == "CREATE INDEX":
            return self._build_create_index()
        elif t == "ALTER TABLE":
            return self._build_alter_table()
        elif t == "DROP TABLE":
            return f"DROP TABLE IF EXISTS {self._full_name()};"
        elif t == "TRUNCATE":
            return f"TRUNCATE TABLE {self._full_name()};"
        elif t == "EXPLAIN SELECT":
            self._type = "SELECT"
            return f"EXPLAIN (FORMAT JSON, ANALYZE false)\n{self._build_select()}"
        elif t == "WITH (CTE)":
            return self._build_cte()
        elif t == "MERGE":
            return self._build_merge()
        else:
            return f"-- Unknown statement type: {t}"

    def _build_select(self) -> str:
        cols = ", ".join(self._columns) if self._columns else "*"
        sql = f"SELECT {'DISTINCT ' if self._distinct else ''}{cols}\nFROM {self._full_name()}"
        for jt, jtbl, jon in self._joins:
            sql += f"\n{jt} {jtbl}\n  ON {jon}"
        if self._where:
            sql += f"\nWHERE {self._where}"
        if self._group_by:
            sql += f"\nGROUP BY {self._group_by}"
            if self._having:
                sql += f"\nHAVING {self._having}"
        if self._order_by:
            sql += f"\nORDER BY {self._order_by}"
        if self._limit is not None:
            sql += f"\nLIMIT {self._limit}"
        if self._offset is not None:
            sql += f"\nOFFSET {self._offset}"
        return sql + ";"

    def _build_insert(self) -> str:
        cols = ""
        if self._insert_columns:
            cols = f" ({', '.join(self._insert_columns)})"
        vals = self._insert_values if self._insert_values else "VALUES (...)"
        return f"INSERT INTO {self._full_name()}{cols}\n{vals};"

    def _build_update(self) -> str:
        if not self._set_clause:
            return f"-- UPDATE: requires SET clause"
        sql = f"UPDATE {self._full_name()}\nSET {self._set_clause}"
        if self._where:
            sql += f"\nWHERE {self._where}"
        return sql + ";"

    def _build_delete(self) -> str:
        sql = f"DELETE FROM {self._full_name()}"
        if self._where:
            sql += f"\nWHERE {self._where}"
        return sql + ";"

    def _build_create_table(self) -> str:
        if not self._create_definition:
            return f"-- CREATE TABLE: requires column definitions"
        return f"CREATE TABLE {self._full_name()} (\n{self._create_definition}\n);"

    def _build_create_index(self) -> str:
        name = self._index_name or f"idx_{self._table}_{self._index_columns.replace(', ', '_')}"
        unique = "UNIQUE " if self._index_unique else ""
        return f"CREATE {unique}INDEX {name}\n  ON {self._full_name()}\n  USING {self._index_method}\n  ({self._index_columns});"

    def _build_alter_table(self) -> str:
        if not self._alter_action:
            return f"-- ALTER TABLE: requires action"
        return f"ALTER TABLE {self._full_name()}\n  {self._alter_action};"

    def _build_cte(self) -> str:
        if not self._cte_name or not self._cte_query:
            return f"-- CTE: requires name and query"
        # The main query is whatever was set as SELECT
        main = self._build_select() if self._columns else "SELECT *"
        return f"WITH {self._cte_name} AS (\n{self._cte_query}\n)\n{main}"

    def _build_merge(self) -> str:
        if not all([self._merge_target, self._merge_using, self._merge_on]):
            return f"-- MERGE: requires target, using, and on clause"
        sql = f"MERGE INTO {self._merge_target} AS target\nUSING {self._merge_using} AS source\nON {self._merge_on}"
        if self._merge_matched:
            sql += f"\nWHEN MATCHED THEN {self._merge_matched}"
        if self._merge_not_matched:
            sql += f"\nWHEN NOT MATCHED THEN {self._merge_not_matched}"
        return sql + ";"

    def preview(self) -> str:
        """Возвращает однострочное описание того, что строим."""
        t = self._type
        if t == "SELECT":
            cols = ", ".join(self._columns[:3])
            if len(self._columns) > 3:
                cols += "..."
            return f"SELECT {cols or '*'} FROM {self._full_name()}"
        elif t == "INSERT":
            return f"INSERT INTO {self._full_name()}"
        elif t == "UPDATE":
            return f"UPDATE {self._full_name()}"
        elif t == "DELETE":
            return f"DELETE FROM {self._full_name()}"
        else:
            return f"{t} → {self._full_name()}"


def stmt_type_choices() -> list[str]:
    return list(_STMT_TYPES)


def join_type_choices() -> list[str]:
    return list(_JOIN_TYPES)
