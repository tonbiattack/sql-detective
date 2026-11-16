"""読み取り専用SQLite実行基盤（Issue #2）。

プレイヤーが書けるのは ``SELECT``（および ``WITH`` で始まるCTE）のみ。
DBは読み取り専用URIで開き、実行時間と取得行数に上限をかけ、
捜査コスト（1回 or 2回）を返す。
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

# --- 仕様値（docs/SPEC.md と対応） -----------------------------------------

MAX_ROWS = 100
QUERY_TIMEOUT_MS = 100
ALLOWED_LEADING_KEYWORDS = frozenset({"SELECT", "WITH"})

TABLE_ORDER = ("users", "orders", "logins", "transfers")

TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "users": ("id", "name", "department", "joined_at", "is_admin"),
    "orders": ("id", "user_id", "amount", "created_at", "status"),
    "logins": ("id", "user_id", "logged_in_at", "ip", "success"),
    "transfers": ("id", "from_user_id", "to_user_id", "amount", "created_at", "memo"),
}

SCHEMA_SQL = """
CREATE TABLE users (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    department TEXT    NOT NULL,
    joined_at  TEXT    NOT NULL,
    is_admin   INTEGER NOT NULL
);

CREATE TABLE orders (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id),
    amount     INTEGER NOT NULL,
    created_at TEXT    NOT NULL,
    status     TEXT    NOT NULL
);

CREATE TABLE logins (
    id           INTEGER PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id),
    logged_in_at TEXT    NOT NULL,
    ip           TEXT    NOT NULL,
    success      INTEGER NOT NULL
);

CREATE TABLE transfers (
    id           INTEGER PRIMARY KEY,
    from_user_id INTEGER NOT NULL REFERENCES users(id),
    to_user_id   INTEGER NOT NULL REFERENCES users(id),
    amount       INTEGER NOT NULL,
    created_at   TEXT    NOT NULL,
    memo         TEXT    NOT NULL
);
"""


# --- 例外 -------------------------------------------------------------------


class QueryRejected(Exception):
    """実行前チェック、またはSQLエラーで拒否されたクエリ。"""


class QueryTimeout(Exception):
    """実行時間の上限を超えたクエリ。"""


# --- 結果 -------------------------------------------------------------------


@dataclass(frozen=True)
class QueryResult:
    """1回の捜査クエリの結果。"""

    sql: str
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    truncated: bool
    elapsed_ms: float
    cost: int

    @property
    def row_count(self) -> int:
        return len(self.rows)


# --- SQLの静的解析 ----------------------------------------------------------


def _mask_literals(sql: str) -> str:
    """文字列リテラルの中身を空白に置き換える（キーワード判定用）。"""
    out: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"":
            quote = ch
            out.append(" ")
            i += 1
            while i < n:
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            out.append(" ")
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _split_statements(sql: str) -> list[str]:
    """コメントと文字列リテラルを考慮して ``;`` で文を分割する。"""
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            while i < n:
                buf.append(sql[i])
                if sql[i] == quote:
                    if i + 1 < n and sql[i + 1] == quote:
                        buf.append(sql[i + 1])
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
        elif sql.startswith("--", i):
            newline = sql.find("\n", i)
            i = n if newline < 0 else newline
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif ch == ";":
            statements.append("".join(buf))
            buf = []
            i += 1
        else:
            buf.append(ch)
            i += 1
    statements.append("".join(buf))
    return [stmt.strip() for stmt in statements if stmt.strip()]


def has_limit_clause(sql: str) -> bool:
    """LIMIT句があるか（文字列リテラル内は無視）。"""
    return re.search(r"\blimit\b", _mask_literals(sql), re.IGNORECASE) is not None


def estimate_cost(sql: str) -> int:
    """実行前のコスト見積り（上限値）。

    LIMITのないクエリは複数行を返しうるため、最大コスト2として扱う。
    """
    return 1 if has_limit_clause(sql) else 2


def validate_statement(sql: str) -> str:
    """SELECT/WITH 1文だけであることを確認し、正規化した文を返す。"""
    if not isinstance(sql, str) or not sql.strip():
        raise QueryRejected("クエリが空です。SELECT 文を入力してください。")

    statements = _split_statements(sql)
    if not statements:
        raise QueryRejected("クエリが空です。SELECT 文を入力してください。")
    if len(statements) > 1:
        raise QueryRejected("1回に実行できるのは1つのSELECT文だけです。")

    statement = statements[0]
    masked = _mask_literals(statement).strip()
    keyword = masked.split(None, 1)[0].upper() if masked else ""
    if keyword not in ALLOWED_LEADING_KEYWORDS:
        shown = keyword or "(不明)"
        raise QueryRejected(
            f"SELECT（またはWITH）以外は実行できません: {shown}。"
            " このDBは読み取り専用です。"
        )
    return statement


# --- 実行基盤 ---------------------------------------------------------------


class ReadOnlyDatabase:
    """読み取り専用で開いたSQLiteへのクエリ実行器。"""

    def __init__(
        self,
        path: str | Path,
        *,
        timeout_ms: int = QUERY_TIMEOUT_MS,
        max_rows: int = MAX_ROWS,
    ) -> None:
        self.path = Path(path)
        if not self.path.is_file():
            raise FileNotFoundError(f"DBファイルが見つかりません: {self.path}")
        self.timeout_ms = timeout_ms
        self.max_rows = max_rows
        self._conn = self._connect()

    # -- 接続 ---------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
        # mode=ro に加えて query_only でも書き込みを塞ぐ（二重の防御）
        conn.execute("PRAGMA query_only = ON")
        return conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ReadOnlyDatabase":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- 実行 ---------------------------------------------------------------

    def validate(self, sql: str) -> str:
        return validate_statement(sql)

    def execute(self, sql: str) -> QueryResult:
        statement = self.validate(sql)
        deadline = time.perf_counter() + self.timeout_ms / 1000.0

        def _abort_if_slow() -> int:
            return 1 if time.perf_counter() > deadline else 0

        self._conn.set_progress_handler(_abort_if_slow, 1000)
        started = time.perf_counter()
        try:
            cursor = self._conn.execute(statement)
            columns = tuple(desc[0] for desc in (cursor.description or ()))
            fetched = cursor.fetchmany(self.max_rows + 1)
        except sqlite3.OperationalError as exc:
            if "interrupt" in str(exc).lower():
                raise QueryTimeout(
                    f"クエリが{self.timeout_ms}msを超えたため中断しました。"
                    " 対象を絞り込んでください。"
                ) from exc
            raise QueryRejected(f"SQLエラー: {exc}") from exc
        except sqlite3.Error as exc:
            raise QueryRejected(f"SQLエラー: {exc}") from exc
        finally:
            self._conn.set_progress_handler(None, 0)
            elapsed_ms = (time.perf_counter() - started) * 1000.0

        truncated = len(fetched) > self.max_rows
        rows = tuple(tuple(row) for row in fetched[: self.max_rows])
        cost = 2 if (not has_limit_clause(statement) and len(rows) > 1) else 1

        return QueryResult(
            sql=statement,
            columns=columns,
            rows=rows,
            truncated=truncated,
            elapsed_ms=elapsed_ms,
            cost=cost,
        )


__all__ = [
    "ALLOWED_LEADING_KEYWORDS",
    "MAX_ROWS",
    "QUERY_TIMEOUT_MS",
    "SCHEMA_SQL",
    "TABLE_COLUMNS",
    "TABLE_ORDER",
    "QueryRejected",
    "QueryResult",
    "QueryTimeout",
    "ReadOnlyDatabase",
    "estimate_cost",
    "has_limit_clause",
    "validate_statement",
]
