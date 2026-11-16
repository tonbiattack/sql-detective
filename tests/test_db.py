"""Issue #2: 読み取り専用クエリ実行基盤のテスト。"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from sql_detective.case import generate_case
from sql_detective.db import (
    MAX_ROWS,
    SCHEMA_SQL,
    QueryRejected,
    ReadOnlyDatabase,
    estimate_cost,
    has_limit_clause,
    validate_statement,
)


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    case = generate_case(7)
    return case.write_db(tmp_path / "case.db")


@pytest.fixture()
def db(db_path: Path):
    with ReadOnlyDatabase(db_path) as handle:
        yield handle


# --- SELECT 以外の拒否 ------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM users",
        "UPDATE users SET name = 'x'",
        "INSERT INTO users VALUES (99, 'x', 'y', 'z', 0)",
        "DROP TABLE users",
        "CREATE TABLE evil (id INTEGER)",
        "ALTER TABLE users ADD COLUMN evil TEXT",
        "PRAGMA writable_schema = ON",
        "ATTACH DATABASE 'evil.db' AS evil",
        "VACUUM",
        "REPLACE INTO users VALUES (1, 'x', 'y', 'z', 0)",
    ],
)
def test_rejects_non_select(db: ReadOnlyDatabase, sql: str) -> None:
    with pytest.raises(QueryRejected):
        db.execute(sql)


def test_rejects_multiple_statements(db: ReadOnlyDatabase) -> None:
    with pytest.raises(QueryRejected, match="1つのSELECT文だけ"):
        db.execute("SELECT 1; SELECT 2")


def test_rejects_empty(db: ReadOnlyDatabase) -> None:
    with pytest.raises(QueryRejected):
        db.execute("   ")


def test_allows_select_and_cte(db: ReadOnlyDatabase) -> None:
    assert db.execute("SELECT COUNT(*) FROM users").rows[0][0] > 0
    assert db.execute("WITH x AS (SELECT 1 AS n) SELECT n FROM x").rows == ((1,),)


def test_leading_comment_is_ignored(db: ReadOnlyDatabase) -> None:
    result = db.execute("-- 調査メモ\nSELECT COUNT(*) FROM users")
    assert result.rows[0][0] > 0


def test_keyword_inside_string_literal_is_not_rejected(db: ReadOnlyDatabase) -> None:
    result = db.execute("SELECT 'DELETE FROM users' AS note")
    assert result.rows == (("DELETE FROM users",),)


def test_semicolon_inside_string_literal_is_one_statement(db: ReadOnlyDatabase) -> None:
    result = db.execute("SELECT 'a;b' AS note")
    assert result.rows == (("a;b",),)


# --- 読み取り専用の担保 -----------------------------------------------------


def test_database_file_is_opened_read_only(db_path: Path) -> None:
    with ReadOnlyDatabase(db_path) as handle:
        with pytest.raises(QueryRejected):
            handle.execute("DELETE FROM users")


def test_query_only_pragma_blocks_writes(db_path: Path) -> None:
    """SELECT を装った書き込み（CTE 経由の DELETE 等）も塞がれていること。"""
    with ReadOnlyDatabase(db_path) as handle:
        with pytest.raises(QueryRejected):
            handle.execute(
                "WITH x AS (SELECT 1) DELETE FROM users WHERE id IN (SELECT * FROM x)"
            )


def test_source_file_is_unmodified_after_queries(db_path: Path) -> None:
    before = db_path.read_bytes()
    with ReadOnlyDatabase(db_path) as handle:
        handle.execute("SELECT * FROM users LIMIT 5")
    assert db_path.read_bytes() == before


# --- 取得上限 ---------------------------------------------------------------


def test_row_cap_truncates(db_path: Path) -> None:
    with ReadOnlyDatabase(db_path) as handle:
        result = handle.execute(
            "SELECT u.id, l.id FROM users AS u CROSS JOIN logins AS l"
        )
    assert result.row_count == MAX_ROWS
    assert result.truncated is True


def test_small_result_is_not_truncated(db: ReadOnlyDatabase) -> None:
    result = db.execute("SELECT * FROM users LIMIT 3")
    assert result.row_count == 3
    assert result.truncated is False


# --- コスト計測 -------------------------------------------------------------


def test_limit_query_costs_one(db: ReadOnlyDatabase) -> None:
    assert db.execute("SELECT * FROM users LIMIT 5").cost == 1


def test_unlimited_multi_row_query_costs_two(db: ReadOnlyDatabase) -> None:
    assert db.execute("SELECT * FROM users").cost == 2


def test_unlimited_single_row_query_costs_one(db: ReadOnlyDatabase) -> None:
    """LIMIT なしでも1行しか返らない集計はコスト1（結果ベース）。"""
    assert db.execute("SELECT COUNT(*) FROM users").cost == 1


def test_estimate_cost_is_upper_bound(db: ReadOnlyDatabase) -> None:
    for sql in ("SELECT * FROM users", "SELECT * FROM users LIMIT 2"):
        assert estimate_cost(sql) >= db.execute(sql).cost


def test_has_limit_clause() -> None:
    assert has_limit_clause("SELECT * FROM users LIMIT 1")
    assert not has_limit_clause("SELECT * FROM users")
    assert not has_limit_clause("SELECT 'limit' AS word")


# --- 実行時間の上限 ---------------------------------------------------------


def test_timeout_interrupts_slow_query(db_path: Path) -> None:
    from sql_detective.db import QueryTimeout

    with ReadOnlyDatabase(db_path, timeout_ms=1) as handle:
        with pytest.raises(QueryTimeout):
            handle.execute(
                "WITH RECURSIVE r(n) AS ("
                "  SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5000000"
                ") SELECT COUNT(*) FROM r"
            )


# --- スキーマ -----------------------------------------------------------------


def test_schema_has_four_fixed_tables(db_path: Path) -> None:
    conn = sqlite3.connect(db_path)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    finally:
        conn.close()
    assert names == {"users", "orders", "logins", "transfers"}


def test_schema_sql_matches_expected_tables() -> None:
    for table in ("users", "orders", "logins", "transfers"):
        assert f"CREATE TABLE {table}" in SCHEMA_SQL


# --- 静的解析の単体 ---------------------------------------------------------


def test_validate_statement_normalizes() -> None:
    assert validate_statement("  SELECT 1  ") == "SELECT 1"


def test_validate_statement_rejects_ddl() -> None:
    with pytest.raises(QueryRejected):
        validate_statement("DROP TABLE users")
